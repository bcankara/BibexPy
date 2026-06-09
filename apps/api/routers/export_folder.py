"""Belirli bir lokal klasöre kopyalama — self-hosted için.

Self-hosted tek kullanıcı senaryosunda backend ve kullanıcı aynı makinededir;
backend'in yazma yetkisi olan herhangi bir klasöre çıktıyı bırakabilir.
"""

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import audit, storage

router = APIRouter(prefix="/projects/{project_id}/export-folder", tags=["export-folder"])


class CopyRequest(BaseModel):
    files: list[str]            # exports/ altındaki dosya adları
    target_folder: str          # mutlak path (örn. C:\Users\me\Desktop\bibex_out)
    create_if_missing: bool = True


class CopyReport(BaseModel):
    target_folder: str
    copied: list[str]
    skipped: list[dict[str, Any]]


@router.post("", response_model=CopyReport)
def copy_to_folder(project_id: str, payload: CopyRequest):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")

    target = Path(payload.target_folder).expanduser()
    if not target.is_absolute():
        raise HTTPException(400, "target_folder_not_absolute")

    if not target.exists():
        if payload.create_if_missing:
            try:
                target.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                raise HTTPException(400, f"folder_create_failed: {e}")
        else:
            raise HTTPException(400, "target_folder_missing")

    exports = storage.project_dir(project_id) / "exports"
    copied: list[str] = []
    skipped: list[dict[str, Any]] = []

    for name in payload.files:
        name = Path(name).name  # safety
        src = exports / name
        if not src.exists():
            skipped.append({"name": name, "reason": "Kaynak dosya bulunamadı"})
            continue
        dst = target / name
        try:
            shutil.copy2(src, dst)
            copied.append(str(dst))
        except Exception as e:
            skipped.append({"name": name, "reason": str(e)})

    audit.write(
        project_id,
        kind="export",
        title=f"{len(copied)} dosya lokal klasöre kopyalandı",
        title_key="audit.titles.filesCopiedToFolder",
        title_params={"n": len(copied)},
        details={
            "target_folder": str(target),
            "copied_count": len(copied),
            "skipped_count": len(skipped),
            "copied": copied,
            "skipped": skipped,
        },
        user_action="copy_to_folder",
    )
    return CopyReport(target_folder=str(target), copied=copied, skipped=skipped)


@router.get("/suggest", response_model=dict)
def suggest_folders():
    """Önerilen çıktı klasörleri — kullanıcının ev dizini altında."""
    home = Path.home()
    out = {
        "home": str(home),
        "suggestions": [
            {"label": "İndirilenler", "path": str(home / "Downloads")},
            {"label": "Belgeler", "path": str(home / "Documents")},
            {"label": "Masaüstü", "path": str(home / "Desktop")},
            {"label": "BibexPy çıktı", "path": str(home / "Documents" / "BibexPy")},
        ],
    }
    return out
