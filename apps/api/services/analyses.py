"""Management of merge analysis runs.

Each merge is stored as a self-contained analysis folder, so previous runs
are preserved, results from different algorithms coexist, the active analysis
can be switched, and snapshot/rollback operations stay safe. Provides path
helpers and CRUD operations over the per-project analyses directory, tracking
the currently active analysis via an active.json pointer.
"""

from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from services import dataset_io, storage


# ─────────────────────────────────────────────────────────────
#  Path helpers
# ─────────────────────────────────────────────────────────────

def analyses_dir(project_id: str) -> Path:
    p = storage.project_dir(project_id) / "analyses"
    p.mkdir(parents=True, exist_ok=True)
    return p


def active_pointer_path(project_id: str) -> Path:
    return analyses_dir(project_id) / "active.json"


def analysis_dir(project_id: str, analysis_id: str) -> Path:
    return analyses_dir(project_id) / analysis_id


# ─────────────────────────────────────────────────────────────
#  CRUD
# ─────────────────────────────────────────────────────────────

def _write_meta(adir: Path, meta: dict[str, Any]) -> None:
    (adir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_meta(adir: Path) -> dict[str, Any]:
    p = adir / "meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def create_analysis(project_id: str, method: str, label: Optional[str] = None) -> tuple[str, Path]:
    """Create a new analysis — timestamped folder + meta.json. Not made active.

    Returns: (analysis_id, path)
    """
    now = time.time()
    stamp = datetime.fromtimestamp(now).strftime("%Y%m%d_%H%M%S")
    analysis_id = f"analysis_{stamp}_{method}"

    # On a collision, append a suffix
    base = analyses_dir(project_id)
    i = 1
    while (base / analysis_id).exists():
        analysis_id = f"analysis_{stamp}_{method}_{i}"
        i += 1

    target = analysis_dir(project_id, analysis_id)
    target.mkdir(parents=True, exist_ok=False)

    # Auto labels are language-neutral ("Smart — 2026-11-04 14:30"); the UI composes
    # a localized display label whenever ``label_auto`` is true.
    label_auto = not label
    if not label:
        label = f"{method.title()} — {datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M')}"

    meta = {
        "id": analysis_id,
        "created_at": now,
        "method": method,
        "label": label,
        "label_auto": label_auto,
        "file_count": 0,
        "source": "user_run",
    }
    _write_meta(target, meta)
    return analysis_id, target


def finalize_analysis(project_id: str, analysis_id: str) -> None:
    """When the analysis completes, update file_count + make it active."""
    adir = analysis_dir(project_id, analysis_id)
    if not adir.exists():
        return
    meta = _read_meta(adir)
    meta["file_count"] = sum(1 for p in adir.iterdir() if p.is_file() and p.name != "meta.json")
    meta["completed_at"] = time.time()
    _write_meta(adir, meta)
    set_active_analysis(project_id, analysis_id)


def list_analyses(project_id: str) -> list[dict[str, Any]]:
    """List all analyses, sorted from newest to oldest."""
    out: list[dict[str, Any]] = []
    active_id = get_active_analysis_id(project_id)
    base = analyses_dir(project_id)
    if not base.exists():
        return out

    for p in base.iterdir():
        if not p.is_dir():
            continue
        # Skip trash folders (deleted but not yet physically cleaned up)
        if p.name.startswith("_deleted_"):
            continue
        meta = _read_meta(p)
        # Skip soft-deleted analyses (deleted: true in meta.json)
        if meta.get("deleted"):
            continue
        if not meta:
            # If meta.json is missing, generate basic info
            meta = {
                "id": p.name,
                "created_at": p.stat().st_mtime,
                "method": "unknown",
                "label": p.name,
                "file_count": sum(1 for f in p.iterdir() if f.is_file()),
            }
        meta["is_active"] = (meta.get("id") == active_id)
        # Analyses created before auto labels were flagged are treated as user labels;
        # the UI also recognizes the legacy auto-label pattern on its own.
        meta.setdefault("label_auto", False)
        # Size information
        try:
            meta["total_size"] = sum(f.stat().st_size for f in p.iterdir() if f.is_file())
        except Exception:
            meta["total_size"] = 0
        out.append(meta)

    out.sort(key=lambda m: m.get("created_at", 0), reverse=True)
    return out


def get_analysis_meta(project_id: str, analysis_id: str) -> Optional[dict[str, Any]]:
    adir = analysis_dir(project_id, analysis_id)
    if not adir.exists():
        return None
    meta = _read_meta(adir)
    # Soft-deleted analyses are treated as non-existent — they cannot be deleted again
    if meta.get("deleted"):
        return None
    meta["is_active"] = (meta.get("id") == get_active_analysis_id(project_id))
    meta.setdefault("label_auto", False)
    return meta or None


def delete_analysis(project_id: str, analysis_id: str) -> bool:
    """Delete an analysis — Windows-friendly **soft delete** strategy.

    On Windows, open file handles (pandas read_excel cache, openpyxl internal
    state, etc.) lock the file against `unlink`/`rename`. To work around this:

      1. If the active analysis is being deleted, move the active pointer
         to another analysis
      2. Clear the filter_engine DataFrame cache + gc.collect()
      3. **Fast path first**: try shutil.rmtree (1 retry).
      4. **Fallback (soft delete)**: write a `"deleted": true` flag into
         meta.json. list_analyses() filters it out; it is invisible in the
         UI. The disk space is reclaimed on the next backend restart via
         `purge_soft_deleted()`.

    Thanks to soft delete, DELETE never raises a 500; the user experience
    stays consistent.
    """
    import gc
    import time as _time

    adir = analysis_dir(project_id, analysis_id)
    if not adir.exists():
        return False

    active_id = get_active_analysis_id(project_id)

    # 1) If the active analysis is being deleted, move the active pointer first
    if active_id == analysis_id:
        remaining = [a for a in list_analyses(project_id)
                     if a["id"] != analysis_id and not is_unfinished(a)]
        if remaining:
            set_active_analysis(project_id, remaining[0]["id"])
        else:
            clear_active_analysis(project_id)

    # 2) Clear the filter cache
    try:
        from services import filter_engine
        filter_engine._DF_CACHE.clear()
    except Exception:
        pass
    gc.collect()

    # 3) Fast path: try a real delete (often fails on Windows)
    def _on_rmtree_error(func, path, exc_info):
        try:
            import os, stat
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            raise

    for _ in range(2):
        try:
            shutil.rmtree(adir, onerror=_on_rmtree_error)
            return True
        except OSError:
            gc.collect()
            _time.sleep(0.3)

    # 4) Soft delete fallback — write the flag into meta.json
    meta_path = adir / "meta.json"
    try:
        meta = _read_meta(adir)
    except Exception:
        meta = {}
    meta["deleted"] = True
    meta["deleted_at"] = _time.time()
    try:
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except Exception:
        # If even meta.json cannot be written, it is truly locked — fail
        if active_id == analysis_id:
            try:
                set_active_analysis(project_id, analysis_id)
            except Exception:
                pass
        return False
    return True


def purge_soft_deleted(project_id: str) -> int:
    """Try to clean up analysis folders that were soft-deleted but not yet
    physically removed. Can be called at backend startup or periodically.
    Returns the number of folders successfully deleted."""
    import gc
    base = analyses_dir(project_id)
    if not base.exists():
        return 0
    purged = 0
    for p in base.iterdir():
        if not p.is_dir():
            continue
        meta = _read_meta(p)
        if not meta.get("deleted"):
            continue
        gc.collect()
        try:
            shutil.rmtree(p, ignore_errors=True)
            if not p.exists():
                purged += 1
        except Exception:
            pass
    return purged


# ─────────────────────────────────────────────────────────────
#  Active analysis
# ─────────────────────────────────────────────────────────────

def get_active_analysis_id(project_id: str) -> Optional[str]:
    p = active_pointer_path(project_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("id")
    except Exception:
        return None


def set_active_analysis(project_id: str, analysis_id: str) -> None:
    p = active_pointer_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"id": analysis_id, "set_at": time.time()}, indent=2), encoding="utf-8")


def clear_active_analysis(project_id: str) -> None:
    p = active_pointer_path(project_id)
    if p.exists():
        p.unlink()


def is_unfinished(meta: dict) -> bool:
    """An analysis a merge created (`source: user_run`) but never finalized."""
    return meta.get("source") == "user_run" and not meta.get("completed_at")


def get_active_analysis_dir(project_id: str) -> Optional[Path]:
    """Return the folder path of the active analysis.

    If there is no active analysis, automatically make the most recent
    analysis active. Returns None if there are no analyses at all.
    """
    active_id = get_active_analysis_id(project_id)
    if active_id:
        adir = analysis_dir(project_id, active_id)
        if adir.exists():
            return adir
        # The active folder was deleted; clear the pointer
        clear_active_analysis(project_id)

    # If there is no active one, use the newest FINISHED one (a merge that is still
    # running, or was killed mid-way, has no completed_at yet).
    analyses = [a for a in list_analyses(project_id) if not is_unfinished(a)]
    if analyses:
        latest = analyses[0]["id"]
        set_active_analysis(project_id, latest)
        return analysis_dir(project_id, latest)

    return None


def work_dir(project_id: str) -> Path:
    """The folder where analysis-specific outputs (disambiguation suggestions,
    snapshots, audit) are written = the ACTIVE analysis folder. This way every
    new merge (new analysis) gets its own clean workspace, and old
    suggestions/snapshots are not carried over.

    If there are no analyses at all, falls back to the project root (backward
    compatibility; in practice these paths are not called without a merge).
    """
    adir = get_active_analysis_dir(project_id)
    return adir if adir is not None else storage.project_dir(project_id)


# ─────────────────────────────────────────────────────────────
#  Dataset path resolver (for filter_engine, merger)
# ─────────────────────────────────────────────────────────────

# Main dataset file name in the active analysis folder (Smart Merge → merged.parquet).
# ORDER MATTERS: parquet comes FIRST; the moment the lazy migration atomically
# publishes the parquet, the resolver switches to it, so a "neither exists"
# window never occurs.
_DATASET_NAMES = (
    "merged.parquet",
    "merged.xlsx",
)


def _audit_migration(project_id: str):
    """on_migrate callback: write the one-time format migration to History.

    The migration snapshot would otherwise not be visible in the UI — the
    restore button depends on the snapshot field of the audit record. audit is
    imported lazily (symmetric with audit's lazy import of analyses; no
    module-level cycle).
    """
    def cb(target: Path, snapshot: Optional[Path]) -> None:
        from services import audit
        audit.write(
            project_id,
            kind="format_migration",
            title="Dataset migrated to Parquet format (one-time speedup)",
            title_key="audit.titles.parquetMigrated",
            details={"dataset": target.name},
            snapshot=(str(snapshot.relative_to(storage.settings.storage_path))
                      if snapshot is not None else None),
            user_action="auto",
        )
    return cb


def active_dataset_path(project_id: str) -> Optional[Path]:
    """Return the main merged dataset file in the active analysis.

    Legacy xlsx datasets are lazily migrated to parquet here — at this SINGLE
    point; `merged_dataset_path` and `load_merged` pick this up automatically.
    """
    adir = get_active_analysis_dir(project_id)
    if adir is None:
        return None
    on_migrate = _audit_migration(project_id)
    for name in _DATASET_NAMES:
        p = adir / name
        if p.exists():
            return dataset_io.ensure_parquet(p, on_migrate=on_migrate)
    # Fallback: the first dataset file (skip side files)
    SKIP = {"statistic.xlsx", "statistic_smart.xlsx", "match_audit.xlsx",
            "conflict_log.xlsx", "borderline_queue.xlsx"}
    for f in sorted(adir.iterdir()):
        if (f.suffix.lower() in dataset_io.DATASET_SUFFIXES
                and f.name.lower() not in SKIP
                and not f.name.lower().startswith("lost_")):
            return dataset_io.ensure_parquet(f, on_migrate=on_migrate)
    return None
