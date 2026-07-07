from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import audit, filter_engine, storage

router = APIRouter(prefix="/projects/{project_id}/filter", tags=["filter"])


class FilterSpec(BaseModel):
    year: Optional[dict[str, Any]] = None
    citation_count: Optional[dict[str, Any]] = None
    doc_type: Optional[list[str]] = None
    language: Optional[list[str]] = None
    db_source: Optional[list[str]] = None
    journal: Optional[list[str]] = None
    authors: Optional[list[str]] = None
    wc_categories: Optional[list[str]] = None
    sc_categories: Optional[list[str]] = None
    fulltext: Optional[dict[str, Any]] = None
    quality: Optional[dict[str, Any]] = None


class FilterRequest(BaseModel):
    spec: FilterSpec = Field(default_factory=FilterSpec)
    offset: int = 0
    limit: int = 50
    columns: Optional[list[str]] = None
    include_facets: bool = True


@router.post("")
def filter_records(project_id: str, payload: FilterRequest):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    try:
        df = filter_engine.load_merged(project_id)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))

    spec_dict = {k: v for k, v in payload.spec.model_dump().items() if v is not None}
    filtered = filter_engine.apply_filter(df, spec_dict)

    result = filter_engine.paginate(
        filtered,
        offset=max(0, payload.offset),
        limit=max(1, min(500, payload.limit)),
        columns=payload.columns,
    )

    if payload.include_facets:
        result["facets"] = filter_engine.compute_facets(filtered)
        result["facets_all"] = filter_engine.compute_facets(df)

    return result


class ApplyFilterRequest(BaseModel):
    spec: FilterSpec = Field(default_factory=FilterSpec)


@router.post("/apply")
def apply_filter_permanently(project_id: str, payload: ApplyFilterRequest):
    """Aktif filtreyi veri setine KALICI uygula: eşleşmeyen satırlar çıkarılır.

    Böylece sonraki adımlar (Harmonizasyon, Export) filtrelenmiş veriyle
    çalışır — filtre yalnızca görünüm olarak kalmaz. Önce snapshot alınır;
    Geçmiş'ten geri yüklenebilir.
    """
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    try:
        df = filter_engine.load_merged(project_id)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))

    spec_dict = {k: v for k, v in payload.spec.model_dump().items() if v is not None}
    if not spec_dict:
        raise HTTPException(400, "no_active_filters")

    filtered = filter_engine.apply_filter(df, spec_dict)
    total_before = int(len(df))
    kept = int(len(filtered))
    removed = total_before - kept
    if removed == 0:
        return {"applied": False, "kept": kept, "removed": 0,
                "total_before": total_before, "snapshot": None}
    if kept == 0:
        raise HTTPException(400, "filter_matches_no_records")

    # Snapshot + kalıcı yaz (records router'daki desenle aynı)
    from routers.records import _save_dataset, _snapshot_dataset
    snapshot = _snapshot_dataset(project_id, df, "filter_apply")
    saved_path = _save_dataset(project_id, filtered.reset_index(drop=True))

    audit.write(
        project_id,
        kind="filter_apply",
        title=f"Filtre veri setine uygulandı: {kept} kayıt kaldı, {removed} çıkarıldı",
        title_key="audit.titles.filterApplied",
        title_params={"kept": kept, "removed": removed},
        details={
            "before": total_before, "after": kept, "removed": removed,
            "filter_keys": list(spec_dict.keys()), "spec": spec_dict,
            "saved_path": saved_path,
        },
        before={"total": total_before},
        after={"total": kept},
        snapshot=snapshot,
        user_action="filter_apply",
    )
    return {"applied": True, "kept": kept, "removed": removed,
            "total_before": total_before, "snapshot": snapshot}


# --- Preset CRUD ---

from pathlib import Path
import json


def _presets_path(project_id: str) -> Path:
    return storage.project_dir(project_id) / "filter_presets.json"


def _load_presets(project_id: str) -> list[dict]:
    p = _presets_path(project_id)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_presets(project_id: str, presets: list[dict]) -> None:
    _presets_path(project_id).write_text(json.dumps(presets, indent=2, ensure_ascii=False), encoding="utf-8")


class PresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    spec: FilterSpec


@router.get("/presets")
def list_presets(project_id: str):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    return _load_presets(project_id)


@router.post("/presets")
def create_preset(project_id: str, payload: PresetCreate):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    presets = _load_presets(project_id)
    presets = [p for p in presets if p.get("name") != payload.name]  # overwrite
    spec_dict = payload.spec.model_dump(exclude_none=True)
    presets.append({"name": payload.name, "spec": spec_dict})
    _save_presets(project_id, presets)
    audit.write(
        project_id,
        kind="filter_save",
        title=f"Preset kaydedildi: {payload.name}",
        title_key="audit.titles.presetSaved",
        title_params={"name": payload.name},
        details={"name": payload.name, "filter_keys": list(spec_dict.keys()), "spec": spec_dict},
        user_action="save_preset",
    )
    return {"ok": True, "count": len(presets)}


@router.delete("/presets/{name}", status_code=204)
def delete_preset(project_id: str, name: str):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    presets = [p for p in _load_presets(project_id) if p.get("name") != name]
    _save_presets(project_id, presets)
    audit.write(
        project_id,
        kind="filter_save",
        title=f"Preset silindi: {name}",
        title_key="audit.titles.presetDeleted",
        title_params={"name": name},
        details={"name": name, "action": "delete"},
        user_action="delete_preset",
    )
    return None
