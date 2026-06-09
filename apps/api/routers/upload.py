from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from models.project import UploadedFile
from services import audit, storage

router = APIRouter(prefix="/projects/{project_id}/files", tags=["upload"])

# Güvenlik: dosya adındaki path-traversal'ı önle
def _safe_name(name: str) -> str:
    base = Path(name).name  # sadece son segment
    if not base or base in {".", ".."}:
        raise HTTPException(400, f"invalid_filename: {name!r}")
    return base


@router.get("", response_model=list[UploadedFile])
def list_files(project_id: str):
    meta = storage.get_project(project_id)
    if meta is None:
        raise HTTPException(404, "project_not_found")
    raw = storage.project_dir(project_id) / "raw"
    if not raw.exists():
        return []
    out: list[UploadedFile] = []
    for f in sorted(raw.iterdir()):
        if not f.is_file():
            continue
        out.append(UploadedFile(
            name=f.name,
            size=f.stat().st_size,
            kind=storage.detect_file_kind(f.name),
            saved_path=str(f.relative_to(storage.settings.storage_path)),
        ))
    return out


@router.post("", response_model=list[UploadedFile])
async def upload_files(project_id: str, files: list[UploadFile] = File(...)):
    meta = storage.get_project(project_id)
    if meta is None:
        raise HTTPException(404, "project_not_found")
    raw = storage.project_dir(project_id) / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    saved: list[UploadedFile] = []
    for upload in files:
        name = _safe_name(upload.filename or "untitled")
        dest = raw / name
        # Aynı isim varsa numara ekle
        i = 1
        stem, suffix = dest.stem, dest.suffix
        while dest.exists():
            dest = raw / f"{stem} ({i}){suffix}"
            i += 1
        # Akışı diske yaz (yığın yığın — büyük dosyalar için)
        with dest.open("wb") as out:
            while chunk := await upload.read(1024 * 1024):
                out.write(chunk)
        saved.append(UploadedFile(
            name=dest.name,
            size=dest.stat().st_size,
            kind=storage.detect_file_kind(dest.name),
            saved_path=str(dest.relative_to(storage.settings.storage_path)),
        ))

    storage.touch_project(project_id)

    # Audit
    total_size = sum(f.size for f in saved)
    audit.write(
        project_id,
        kind="upload",
        title=f"{len(saved)} dosya yüklendi",
        title_key="audit.titles.filesUploaded",
        title_params={"n": len(saved)},
        analysis_id=None,  # proje-seviye: tüm analizlerde "kurulum" olarak görünür
        details={
            "files": [{"name": f.name, "size": f.size, "kind": f.kind} for f in saved],
            "total_size": total_size,
        },
        user_action="upload",
    )
    return saved


@router.delete("/{filename}", status_code=204)
def delete_file(project_id: str, filename: str):
    meta = storage.get_project(project_id)
    if meta is None:
        raise HTTPException(404, "project_not_found")
    name = _safe_name(filename)
    target = storage.project_dir(project_id) / "raw" / name
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "file_not_found")
    size = target.stat().st_size
    target.unlink()
    storage.touch_project(project_id)
    audit.write(
        project_id,
        kind="upload",
        title=f"Dosya silindi: {name}",
        title_key="audit.titles.fileDeleted",
        title_params={"name": name},
        analysis_id=None,
        details={"name": name, "size": size, "action": "delete"},
        user_action="delete_file",
    )
    return None
