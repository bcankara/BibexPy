"""Kayıt seviyesinde işlemler — toplu silme, seçilenleri zenginleştirme.

Her destrüktif işlem önce snapshot alır ve audit log'a yazılır.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import analyses, audit, filter_engine, merger, storage


router = APIRouter(prefix="/projects/{project_id}/records", tags=["records"])


def _snapshot_dataset(project_id: str, df: pd.DataFrame, reason: str) -> str:
    """Mevcut datasetin bir kopyasını AKTİF analizin snapshots/ klasörüne yaz. Path döndür."""
    snaps = analyses.work_dir(project_id) / "snapshots"
    snaps.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    p = snaps / f"pre_{reason}_{stamp}.xlsx"
    df.to_excel(p, index=False)
    rel = str(p.relative_to(storage.settings.storage_path))
    return rel


def _save_dataset(project_id: str, df: pd.DataFrame) -> str:
    """Aktif merged_*.xlsx dosyasının üzerine yaz, cache'i temizle."""
    p = merger.merged_dataset_path(project_id)
    if p is None:
        raise HTTPException(409, "no_active_merged_dataset")
    df.to_excel(p, index=False)
    # cache invalidate
    filter_engine._DF_CACHE.clear()
    return str(p.relative_to(storage.settings.storage_path))


class DeletePayload(BaseModel):
    uids: list[str] = Field(default_factory=list, description="Benzersiz satır UID'leri (önerilen)")
    dois: list[str] = Field(default_factory=list, description="DOI listesi (yalnız DOI'li kayıtlar için)")
    indices: list[int] = Field(default_factory=list, description="Pozisyon tabanlı (fallback, kırılgan)")
    reason: Optional[str] = None


@router.post("/delete")
def delete_records(project_id: str, payload: DeletePayload):
    """UID, DOI veya satır indekslerine göre toplu silme. Snapshot alınır."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    df = filter_engine.load_merged(project_id)
    total_before = int(len(df))

    if not payload.uids and not payload.dois and not payload.indices:
        raise HTTPException(400, "no_records_to_delete")

    keep = pd.Series(True, index=df.index)

    if payload.uids and "UID" in df.columns:
        uid_set = {str(u).strip() for u in payload.uids if u}
        df_uid = df["UID"].astype(str).str.strip()
        keep &= ~df_uid.isin(uid_set)

    if payload.dois and "DI" in df.columns:
        doi_set = {str(d).strip().lower() for d in payload.dois if d}
        df_doi = df["DI"].astype(str).str.strip().str.lower()
        keep &= ~df_doi.isin(doi_set)

    if payload.indices:
        idx_to_drop = set(payload.indices)
        keep &= ~pd.Series([i in idx_to_drop for i in range(len(df))], index=df.index)

    deleted_count = int((~keep).sum())
    if deleted_count == 0:
        return {"deleted": 0, "kept": total_before, "snapshot": None}

    snapshot = _snapshot_dataset(project_id, df, "delete")
    df_new = df.loc[keep].reset_index(drop=True)
    saved_path = _save_dataset(project_id, df_new)

    audit.write(
        project_id,
        kind="records_delete",
        title=f"{deleted_count} kayıt silindi",
        title_key="audit.titles.recordsDeleted",
        title_params={"n": deleted_count},
        details={
            "before": total_before,
            "after": int(len(df_new)),
            "deleted": deleted_count,
            "by_uid": len(payload.uids),
            "by_doi": len(payload.dois),
            "by_index": len(payload.indices),
            "reason": payload.reason,
            "saved_path": saved_path,
        },
        before={"total": total_before},
        after={"total": int(len(df_new))},
        snapshot=snapshot,
        user_action="manual_bulk_delete",
    )

    return {
        "deleted": deleted_count,
        "kept": int(len(df_new)),
        "total_before": total_before,
        "snapshot": snapshot,
    }


class RestoreSnapshotPayload(BaseModel):
    snapshot: str


@router.post("/restore-snapshot")
def restore_snapshot(project_id: str, payload: RestoreSnapshotPayload):
    """Snapshot dosyasını aktif dataset olarak geri yükle."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    snap_path = storage.settings.storage_path / payload.snapshot
    if not snap_path.exists():
        raise HTTPException(404, f"snapshot_not_found: {payload.snapshot}")
    df = pd.read_excel(snap_path)
    saved_path = _save_dataset(project_id, df)
    audit.write(
        project_id,
        kind="snapshot_restore",
        title=f"Snapshot geri yüklendi: {Path(payload.snapshot).name}",
        title_key="audit.titles.snapshotRestored",
        title_params={"name": Path(payload.snapshot).name},
        details={"snapshot": payload.snapshot, "restored_count": int(len(df)), "saved_path": saved_path},
        user_action="restore",
    )
    return {"restored": int(len(df)), "snapshot": payload.snapshot}


@router.get("/snapshots")
def list_snapshots(project_id: str):
    """Mevcut snapshot dosyalarını listele."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    snaps_dir = analyses.work_dir(project_id) / "snapshots"
    if not snaps_dir.exists():
        return []
    items = []
    for f in sorted(snaps_dir.iterdir(), reverse=True):
        if f.is_file() and f.suffix.lower() == ".xlsx":
            items.append({
                "name": f.name,
                "relative_path": str(f.relative_to(storage.settings.storage_path)),
                "size": f.stat().st_size,
                "mtime": f.stat().st_mtime,
            })
    return items


class UpdatePayload(BaseModel):
    uid: Optional[str] = None
    doi: Optional[str] = None
    fields: dict[str, Any] = Field(default_factory=dict, description="Güncellenecek alan -> yeni değer")


@router.post("/update")
def update_record(project_id: str, payload: UpdatePayload):
    """Tek bir kaydın alanlarını elle düzenle (UID veya DOI ile bul). Snapshot alınır."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    if not payload.fields:
        raise HTTPException(400, "no_fields_to_update")
    df = filter_engine.load_merged(project_id)

    mask = pd.Series(False, index=df.index)
    if payload.uid and "UID" in df.columns:
        mask |= df["UID"].astype(str).str.strip() == str(payload.uid).strip()
    if not mask.any() and payload.doi and "DI" in df.columns:
        mask |= df["DI"].astype(str).str.strip().str.lower() == str(payload.doi).strip().lower()
    idxs = df.index[mask].tolist()
    if not idxs:
        raise HTTPException(404, "record_not_found")
    idx = idxs[0]

    snapshot = _snapshot_dataset(project_id, df, "edit")
    for k, v in payload.fields.items():
        if k not in df.columns:
            df[k] = ""
        df.at[idx, k] = "" if v is None else v
    saved_path = _save_dataset(project_id, df)

    audit.write(
        project_id,
        kind="records_edit",
        title=f"Kayıt düzenlendi ({len(payload.fields)} alan)",
        title_key="audit.titles.recordEdited",
        title_params={"n": len(payload.fields)},
        details={"uid": payload.uid, "doi": payload.doi, "fields": list(payload.fields.keys()), "saved_path": saved_path},
        snapshot=snapshot,
        user_action="manual_edit",
    )
    return {"updated": 1, "fields": len(payload.fields), "snapshot": snapshot}
