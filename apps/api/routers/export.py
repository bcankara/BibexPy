from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import audit, exporter, storage

router = APIRouter(prefix="/projects/{project_id}/export", tags=["export"])


class ExportRequest(BaseModel):
    fmt: str
    filter: Optional[dict[str, Any]] = None
    output_name: Optional[str] = None
    # "rename" (default) → yeni timestamp'li dosya, eskiler kalır
    # "replace"          → aynı format'taki tüm eski dosyalar silinir, yenisi yazılır
    # "abort"            → aynı format zaten varsa hata fırlat (409)
    if_exists: str = "rename"


class ExportResult(BaseModel):
    name: str
    size: int
    relative_path: str


def _list_format_files(project_id: str, fmt: str) -> list[Path]:
    """Aynı format'taki mevcut export dosyalarını döner."""
    root = storage.project_dir(project_id)
    exports_dir = root / "exports"
    if not exports_dir.exists():
        return []
    suffix = f".{fmt.lower()}"
    return [f for f in exports_dir.iterdir() if f.is_file() and f.suffix.lower() == suffix]


@router.get("", response_model=list[ExportResult])
def list_exports(project_id: str):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    return exporter.list_exports(project_id)


@router.post("", response_model=ExportResult)
def create_export(project_id: str, payload: ExportRequest):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")

    # Aynı format için mevcut dosyaları if_exists'e göre ele al
    if_exists = (payload.if_exists or "rename").lower()
    if if_exists not in ("rename", "replace", "abort"):
        raise HTTPException(400, "invalid_if_exists")

    existing = _list_format_files(project_id, payload.fmt)
    replaced_count = 0
    if existing:
        if if_exists == "abort":
            raise HTTPException(409, f"format_files_exist: {len(existing)}")
        if if_exists == "replace":
            for f in existing:
                try:
                    f.unlink()
                    replaced_count += 1
                except Exception:
                    pass

    p = exporter.export(project_id, payload.fmt, payload.filter, payload.output_name)
    filtered = bool(payload.filter)
    audit.write(
        project_id,
        kind="export",
        title=f"Export ({payload.fmt.upper()}): {p.name}",
        title_key="audit.titles.exported",
        title_params={"fmt": payload.fmt.upper(), "name": p.name},
        details={
            "format": payload.fmt,
            "output": p.name,
            "output_size": p.stat().st_size,
            "filtered": filtered,
            "filter_keys": list((payload.filter or {}).keys()) if filtered else [],
            "if_exists": if_exists,
            "replaced_count": replaced_count,
        },
        user_action="export",
    )
    return ExportResult(name=p.name, size=p.stat().st_size,
                        relative_path=str(p.relative_to(storage.settings.storage_path)))


@router.delete("/{name}")
def delete_export(project_id: str, name: str):
    """Belirli bir export dosyasını sil."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    # Path-traversal koruması
    safe_name = Path(name).name
    if not safe_name or safe_name != name:
        raise HTTPException(400, "invalid_filename")
    target = storage.project_dir(project_id) / "exports" / safe_name
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "file_not_found")
    try:
        target.unlink()
    except Exception as e:
        raise HTTPException(500, f"delete_failed: {e}")
    audit.write(
        project_id,
        kind="export",
        title=f"Export silindi: {safe_name}",
        title_key="audit.titles.exportDeleted",
        title_params={"name": safe_name},
        details={"deleted": safe_name},
        user_action="export_delete",
    )
    return {"ok": True, "deleted": safe_name}
