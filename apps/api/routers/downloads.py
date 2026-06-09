"""Üretilen dosyalar için genel indirme endpoint'i."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from services import storage

router = APIRouter(prefix="/projects/{project_id}/download", tags=["downloads"])

# İndirilebilir alt klasörler. analyses/ direkt değil, alt klasörleri ile gelir (frontend gerekirse path bildirir).
ALLOWED = {"raw", "processed", "merged", "exports", "snapshots"}


@router.get("/{folder}/{filename}")
def download(project_id: str, folder: str, filename: str):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    if folder not in ALLOWED:
        raise HTTPException(400, f"invalid_folder: {folder}")
    name = Path(filename).name
    target = storage.project_dir(project_id) / folder / name
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "file_not_found")
    return FileResponse(target, filename=name)
