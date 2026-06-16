"""Filesystem browsing endpoints for the Settings directory picker.

Provides a FastAPI router that lets the frontend navigate the backend host's
directory tree: listing drives and subfolders, resolving folder names to full
paths, and exposing quick-access shortcuts. Designed for self-hosted use where
backend and frontend share the same machine.
"""

from __future__ import annotations

import os
import string
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


router = APIRouter(prefix="/system", tags=["system"])


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _list_windows_drives() -> list[str]:
    """Mevcut Windows sürücülerini listele (C:, D:, ...)."""
    drives = []
    for letter in string.ascii_uppercase:
        d = f"{letter}:\\"
        if os.path.exists(d):
            drives.append(d)
    return drives


@router.get("/find-by-name")
def find_by_name(name: str, limit: int = 8):
    """Verilen klasör adının olası tam yollarını bul.

    Kullanım: tarayıcının native picker'ı sadece klasör adı veriyor (path güvenlik
    nedeniyle gizli). Backend, kullanıcının home altında bu adı arar ve olası
    eşleşmeleri döndürür. Kullanıcı UI'da hangisi olduğunu doğrular.
    """
    name = name.strip()
    if not name:
        raise HTTPException(400, "name_empty")

    candidates: list[dict[str, Any]] = []
    home = Path.home()

    # Önce shortcut'larda ara (hızlı)
    search_roots: list[Path] = [
        home,
        home / "Documents",
        home / "Desktop",
        home / "Downloads",
    ]
    if sys.platform == "darwin":
        search_roots.append(Path("/Volumes"))
    elif _is_windows():
        # Drive root'larında da bak
        for d in _list_windows_drives():
            search_roots.append(Path(d))

    seen: set[str] = set()
    for root in search_roots:
        if not root.exists():
            continue
        try:
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                if child.name == name:
                    p = str(child.resolve())
                    if p not in seen:
                        seen.add(p)
                        candidates.append({"path": p, "depth": 1, "parent": str(root)})
                if len(candidates) >= limit:
                    return {"name": name, "matches": candidates}
        except (PermissionError, OSError):
            continue

    # 2. seviye (sadece home altında — sığ recursive)
    if len(candidates) < limit:
        try:
            for child in home.iterdir():
                if not child.is_dir() or child.name.startswith("."):
                    continue
                try:
                    target = child / name
                    if target.exists() and target.is_dir():
                        p = str(target.resolve())
                        if p not in seen:
                            seen.add(p)
                            candidates.append({"path": p, "depth": 2, "parent": str(child)})
                except (PermissionError, OSError):
                    continue
                if len(candidates) >= limit:
                    break
        except (PermissionError, OSError):
            pass

    return {"name": name, "matches": candidates}


@router.get("/browse")
def browse(path: str = ""):
    """Verilen dizinin alt-klasörlerini listele.

    Path boş veya geçersizse:
    - Windows: drive root'larını döndür (C:\\, D:\\, ...)
    - Unix: / döndür
    """
    home = Path.home()

    # Boş path → drive listesi (Windows) veya root (Unix)
    if not path:
        if _is_windows():
            drives = _list_windows_drives()
            return {
                "current": "",
                "parent": None,
                "is_root": True,
                "entries": [
                    {"name": d, "path": d, "is_dir": True, "is_drive": True}
                    for d in drives
                ],
                "shortcuts": _shortcuts(home),
                "platform": "windows",
            }
        else:
            # macOS / Linux için root
            return browse("/")

    try:
        p = Path(path).expanduser()
    except Exception as e:
        raise HTTPException(400, f"invalid_path: {e}")

    if not p.exists():
        raise HTTPException(404, "path_not_found")
    if not p.is_dir():
        raise HTTPException(400, "not_a_folder")

    # Alt klasörleri listele (gizli olanları atla, dosyaları da atla)
    entries: list[dict[str, Any]] = []
    try:
        for child in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            try:
                if child.name.startswith(".") or child.name.startswith("$"):
                    continue
                if not child.is_dir():
                    continue
                entries.append({
                    "name": child.name,
                    "path": str(child),
                    "is_dir": True,
                    "is_drive": False,
                })
            except (PermissionError, OSError):
                continue
    except PermissionError:
        raise HTTPException(403, "folder_access_denied")
    except OSError as e:
        raise HTTPException(500, f"folder_list_failed: {e}")

    # Parent path
    parent_path: str | None = None
    p_abs = p.resolve()
    if _is_windows() and len(p_abs.parts) <= 1:
        # Drive root → parent yok (drive listesine dönülmeli)
        parent_path = ""
    elif p_abs.parent != p_abs:
        parent_path = str(p_abs.parent)

    return {
        "current": str(p_abs),
        "parent": parent_path,
        "is_root": False,
        "entries": entries,
        "shortcuts": _shortcuts(home),
        "platform": "windows" if _is_windows() else "unix",
    }


def _shortcuts(home: Path) -> list[dict[str, str]]:
    """Soldan hızlı erişim kısayolları — OS'a göre uyarlı."""
    items: list[dict[str, str]] = [
        {"label": "Ev", "path": str(home), "icon": "home"},
        {"label": "Belgeler", "path": str(home / "Documents"), "icon": "documents"},
        {"label": "İndirilenler", "path": str(home / "Downloads"), "icon": "downloads"},
        {"label": "Masaüstü", "path": str(home / "Desktop"), "icon": "desktop"},
    ]
    if _is_windows():
        items.append({"label": "Bilgisayar", "path": "", "icon": "computer"})
    elif sys.platform == "darwin":
        # macOS — bağlı sürücüler /Volumes altında
        items.append({"label": "Uygulamalar", "path": "/Applications", "icon": "computer"})
        if Path("/Volumes").exists():
            items.append({"label": "Sürücüler", "path": "/Volumes", "icon": "computer"})
    else:
        # Linux
        if Path("/mnt").exists():
            items.append({"label": "Mount", "path": "/mnt", "icon": "computer"})
        if Path("/media").exists():
            items.append({"label": "Media", "path": "/media", "icon": "computer"})
    # Sadece gerçekten var olan dizinleri döndür
    return [it for it in items if not it["path"] or Path(it["path"]).exists()]
