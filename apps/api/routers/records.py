"""API router for record-level operations on a project's merged dataset.

Provides endpoints to bulk-delete records, edit a single record, and list or
restore dataset snapshots. Rows are addressed by UID only, never by position or DOI.
Every destructive operation takes a snapshot first
and writes an entry to the audit log.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Optional, Union
from uuid import uuid4

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from jobs.runner import job_runner
from services import analyses, audit, dataset_io, filter_engine, merger, pdcompat, storage


router = APIRouter(prefix="/projects/{project_id}/records", tags=["records"])

# What the record editor may write into a numeric column: an optional sign, digits with an
# optional decimal part, and an optional exponent ("2024", "-1.5", "3e2").
_PLAIN_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def _snapshot_dataset(project_id: str, df: pd.DataFrame, reason: str) -> str:
    """Write a copy of the current dataset into the ACTIVE analysis's snapshots/ folder. Return the path."""
    snaps = analyses.work_dir(project_id) / "snapshots"
    snaps.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:6]
    p = snaps / f"pre_{reason}_{stamp}.parquet"
    dataset_io.atomic_write_dataset(df, p)
    rel = str(p.relative_to(storage.settings.storage_path))
    return rel


def _save_dataset(project_id: str, df: pd.DataFrame) -> str:
    """Write the active dataset file ATOMICALLY and clear the cache."""
    p = merger.merged_dataset_path(project_id)
    if p is None:
        raise HTTPException(409, "no_active_merged_dataset")
    dataset_io.atomic_write_dataset(df, p)
    # cache invalidate
    filter_engine._DF_CACHE.clear()
    return str(p.relative_to(storage.settings.storage_path))


class DeletePayload(BaseModel):
    # UID is the ONLY row key: positions shift after every delete/filter/restore and a DOI
    # can be shared by several rows, so neither is accepted.
    uids: list[Union[str, int]] = Field(default_factory=list, description="Unique row UIDs (sequential integers)")
    reason: Optional[str] = None


@router.post("/delete")
def delete_records(project_id: str, payload: DeletePayload):
    """Bulk delete by UID. A snapshot is taken. UIDs that are not in the dataset are skipped and counted."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    uid_set = {k for k in (dataset_io.uid_key(u) for u in payload.uids) if k}
    if not uid_set:
        raise HTTPException(400, "uids_required")
    with job_runner.mutation(project_id):
        try:
            df = filter_engine.load_merged(project_id, already_locked=True)
        except FileNotFoundError as e:
            raise HTTPException(409, str(e))
        total_before = int(len(df))

        df_uid = df[dataset_io.UID_COL].map(dataset_io.uid_key)
        keep = ~df_uid.isin(uid_set)
        found = set(df_uid[~keep])
        requested = len(uid_set)
        not_found = requested - len(found)

        deleted_count = int((~keep).sum())
        if deleted_count == 0:
            return {"deleted": 0, "kept": total_before, "snapshot": None,
                    "requested": requested, "not_found": not_found}

        snapshot = _snapshot_dataset(project_id, df, "delete")
        df_new = df.loc[keep].reset_index(drop=True)
        saved_path = _save_dataset(project_id, df_new)

        audit.write(
            project_id,
            kind="records_delete",
            title=f"{deleted_count} records deleted",
            title_key="audit.titles.recordsDeleted",
            title_params={"n": deleted_count},
            details={
                "before": total_before,
                "after": int(len(df_new)),
                "deleted": deleted_count,
                "by_uid": requested,
                "not_found": not_found,
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
            "requested": requested,
            "not_found": not_found,
        }


class RestoreSnapshotPayload(BaseModel):
    snapshot: str


@router.post("/restore-snapshot")
def restore_snapshot(project_id: str, payload: RestoreSnapshotPayload):
    """Restore the snapshot file as the active dataset."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    with job_runner.mutation(project_id):
        # The path comes from the request body: resolve it and require it to stay inside
        # this project's folder (no "../" or absolute paths to arbitrary files) AND inside
        # the ACTIVE analysis folder: another analysis has its own, unrelated UID set.
        snap_path = (storage.settings.storage_path / payload.snapshot).resolve()
        active_dir = analyses.get_active_analysis_dir(project_id)
        if (
            not snap_path.is_relative_to(storage.project_dir(project_id).resolve())
            or active_dir is None
            or not snap_path.is_relative_to(Path(active_dir).resolve())
            or not snap_path.is_file()
        ):
            raise HTTPException(404, f"snapshot_not_found: {payload.snapshot}")
        # Format converter: snapshots from the xlsx era can be loaded into the parquet dataset.
        df = dataset_io.read_dataset(snap_path)
        # A snapshot that predates UIDs (or carries blank/repeated ones) gets valid UIDs
        # before it becomes the active dataset (we hold the mutation lock here). Rows that
        # already have a key keep it, so a restored row comes back under its original key.
        analyses.ensure_dataset_uid(project_id, df)
        saved_path = _save_dataset(project_id, df)
        # A pre_borderline_accept snapshot carries the borderline state it was taken with:
        # roll the accept decisions back together with the dataset.
        borderline_restored = False
        try:
            from services import smart_merger
            borderline_restored = smart_merger.restore_borderline_state(project_id, snap_path)
        except Exception:
            pass
        audit.write(
            project_id,
            kind="snapshot_restore",
            title=f"Snapshot restored: {Path(payload.snapshot).name}",
            title_key="audit.titles.snapshotRestored",
            title_params={"name": Path(payload.snapshot).name},
            details={"snapshot": payload.snapshot, "restored_count": int(len(df)), "saved_path": saved_path,
                     "borderline_state_restored": borderline_restored},
            user_action="restore",
        )
        return {"restored": int(len(df)), "snapshot": payload.snapshot}


@router.get("/snapshots")
def list_snapshots(project_id: str):
    """List the existing snapshot files."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    snaps_dir = analyses.work_dir(project_id) / "snapshots"
    if not snaps_dir.exists():
        return []
    items = []
    for f in sorted(snaps_dir.iterdir(), reverse=True):
        # Legacy (xlsx) snapshots stay in the list — restoring converts the format.
        if f.is_file() and f.suffix.lower() in {".xlsx", ".parquet"}:
            items.append({
                "name": f.name,
                "relative_path": str(f.relative_to(storage.settings.storage_path)),
                "size": f.stat().st_size,
                "mtime": f.stat().st_mtime,
            })
    return items


class UpdatePayload(BaseModel):
    uid: Optional[Union[str, int]] = None
    fields: dict[str, Any] = Field(default_factory=dict, description="Field to update -> new value")


@router.post("/update")
def update_record(project_id: str, payload: UpdatePayload):
    """Manually edit the fields of a single record, found by its UID only. A snapshot is taken."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    uid = dataset_io.uid_key(payload.uid)
    if not uid:
        raise HTTPException(400, "uids_required")
    if any(str(k).strip().upper() == dataset_io.UID_COL for k in payload.fields):
        raise HTTPException(400, "uid_immutable")
    # Internal columns (leading underscore) are not editable through the API.
    fields = {k: v for k, v in payload.fields.items() if not str(k).startswith("_")}
    if not fields:
        raise HTTPException(400, "no_fields_to_update")
    with job_runner.mutation(project_id):
        try:
            # load_merged returns the SHARED cached frame: edit a copy, so a failed save
            # cannot leave phantom edits in the cache.
            df = filter_engine.load_merged(project_id, already_locked=True).copy()
        except FileNotFoundError as e:
            raise HTTPException(409, str(e))

        # No DOI (or any other) fallback: an unknown UID means the client's view is stale.
        mask = df[dataset_io.UID_COL].map(dataset_io.uid_key) == uid
        idxs = df.index[mask].tolist()
        if not idxs:
            raise HTTPException(404, "record_not_found")
        idx = idxs[0]

        # Reject a value the column cannot hold before anything is snapshotted or written.
        for k, v in fields.items():
            if isinstance(v, (list, dict, set, tuple)):
                raise HTTPException(400, "invalid_field_value")
            if (k in df.columns and pd.api.types.is_numeric_dtype(df[k])
                    and not pd.api.types.is_bool_dtype(df[k])
                    and isinstance(v, str) and v.strip()):
                # A plain decimal number only: float() would also accept "nan", "inf" and
                # "1_0", which the save step cannot store as numbers (the whole column
                # would silently turn into text).
                if not _PLAIN_NUMBER.fullmatch(v.strip()):
                    raise HTTPException(400, "invalid_numeric_value")

        snapshot = _snapshot_dataset(project_id, df, "edit")
        for k, v in fields.items():
            if k not in df.columns:
                df[k] = ""
            # The UI sends strings; set_cell widens a numeric column to object exactly when
            # pandas 3 would raise (pandas 2 did the same silently). The save restores numerics.
            pdcompat.set_cell(df, idx, k, "" if v is None else v)
        saved_path = _save_dataset(project_id, df)

        audit.write(
            project_id,
            kind="records_edit",
            title=f"Record edited ({len(fields)} fields)",
            title_key="audit.titles.recordEdited",
            title_params={"n": len(fields)},
            details={"uid": uid, "fields": list(fields.keys()), "saved_path": saved_path},
            snapshot=snapshot,
            user_action="manual_edit",
        )
        return {"updated": 1, "fields": len(fields), "snapshot": snapshot}
