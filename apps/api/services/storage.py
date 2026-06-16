"""Disk-based project storage.

Manages the on-disk layout for each project (raw uploads, processed
conversions, merge results, exports, and snapshots) and provides CRUD
helpers for project metadata persisted as meta.json.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import settings
from models.project import ProjectMeta


SUBDIRS = ("raw", "processed", "merged", "exports", "snapshots")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def project_dir(project_id: str) -> Path:
    return settings.storage_path / project_id


def meta_path(project_id: str) -> Path:
    return project_dir(project_id) / "meta.json"


def ensure_project_layout(project_id: str) -> Path:
    root = project_dir(project_id)
    root.mkdir(parents=True, exist_ok=True)
    for sub in SUBDIRS:
        (root / sub).mkdir(exist_ok=True)
    return root


def create_project(name: str, description: Optional[str] = None) -> ProjectMeta:
    pid = uuid.uuid4().hex[:12]
    ensure_project_layout(pid)
    meta = ProjectMeta(
        id=pid,
        name=name.strip(),
        description=(description or "").strip() or None,
        created_at=_now(),
        updated_at=_now(),
    )
    _write_meta(meta)
    return meta


def _write_meta(meta: ProjectMeta) -> None:
    meta_path(meta.id).write_text(meta.model_dump_json(indent=2), encoding="utf-8")


def _read_meta(project_id: str) -> Optional[ProjectMeta]:
    p = meta_path(project_id)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return ProjectMeta.model_validate(data)


def list_projects() -> list[ProjectMeta]:
    out: list[ProjectMeta] = []
    for child in settings.storage_path.iterdir():
        if not child.is_dir():
            continue
        meta = _read_meta(child.name)
        if meta is None:
            continue
        _refresh_size(meta)
        out.append(meta)
    out.sort(key=lambda m: m.updated_at, reverse=True)
    return out


def get_project(project_id: str) -> Optional[ProjectMeta]:
    meta = _read_meta(project_id)
    if meta is None:
        return None
    _refresh_size(meta)
    return meta


def _refresh_size(meta: ProjectMeta) -> None:
    raw = project_dir(meta.id) / "raw"
    if not raw.exists():
        meta.file_count = 0
        meta.raw_size_bytes = 0
        return
    files = [f for f in raw.iterdir() if f.is_file()]
    meta.file_count = len(files)
    meta.raw_size_bytes = sum(f.stat().st_size for f in files)


def _robust_rmtree(path: Path) -> None:
    """Windows'ta salt-okunur bit ve geçici dosya kilitlerine dayanıklı silme.

    Çıplak shutil.rmtree, Windows'ta açık/salt-okunur bir dosya olduğunda
    PermissionError (WinError 5/32) fırlatır. Burada salt-okunur bit kaldırılır
    ve kısa beklemelerle birkaç kez denenir.
    """
    import os
    import shutil
    import stat
    import time

    def _on_error(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    last_exc: Exception | None = None
    for _ in range(3):
        try:
            shutil.rmtree(path, onerror=_on_error)
        except Exception as e:  # son denemede yeniden fırlatılır
            last_exc = e
        if not path.exists():
            return
        time.sleep(0.3)
    if path.exists():
        raise last_exc or OSError(f"Klasör silinemedi: {path}")


def delete_project(project_id: str) -> bool:
    root = project_dir(project_id)
    if not root.exists():
        return False
    _robust_rmtree(root)
    return True


def touch_project(project_id: str) -> None:
    meta = _read_meta(project_id)
    if meta is None:
        return
    meta.updated_at = _now()
    _write_meta(meta)


def detect_file_kind(filename: str) -> str:
    name = filename.lower()
    if name.endswith(".csv"):
        return "scopus_csv"
    if name.endswith(".txt") or name.endswith(".isi"):
        return "wos_txt"
    if name.endswith(".xlsx") or name.endswith(".xls"):
        return "xlsx"
    return "unknown"
