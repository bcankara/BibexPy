"""Merge operations wrapper.

Each merge is stored as an independent analysis folder. This module loads the
source datasets, handles the single-source passthrough case (no deduplication),
and dispatches two-source merges to the smart_merger pipeline, which opens and
finalizes its own analysis folder.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import HTTPException

from jobs.runner import JobContext
from services import analyses, converter, dataset_io, storage


def _project_paths(project_id: str) -> tuple[Path, Path, Path]:
    """(root, raw, processed) — the project's data folders."""
    meta = storage.get_project(project_id)
    if meta is None:
        raise HTTPException(404, "project_not_found")
    root = storage.project_dir(project_id)
    return root, root / "raw", root / "processed"


def _find_xlsx(processed: Path, raw: Path, hint: str) -> Path | None:
    """Search processed first, then raw."""
    for d in (processed, raw):
        if not d.exists():
            continue
        for f in d.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() == ".xlsx" and hint in f.name.lower():
                return f
    return None


def check_ready_to_merge(project_id: str) -> None:
    """Called BEFORE the merge starts (without submitting a job). Since preparation
    is now done IMPLICITLY in the first phase of the merge job, raw data is
    sufficient; it raises 409 'no_source_data' only if there is no source data at
    all (the UI directs the user to upload).
    """
    _, raw, processed = _project_paths(project_id)
    has_xlsx = bool(
        _find_xlsx(processed, raw, "scopus") or _find_xlsx(processed, raw, "scp")
        or _find_xlsx(processed, raw, "wos")
    )
    has_raw = raw.exists() and any(
        f.is_file() and f.suffix.lower() in (".csv", ".txt", ".isi", ".xlsx")
        for f in raw.iterdir()
    )
    if has_xlsx or has_raw:
        return
    raise HTTPException(409, "no_source_data")


def _input_paths(project_id: str) -> tuple[Path | None, Path | None]:
    """Resolve the consolidated Scopus/WoS XLSX paths (WITHOUT loading — for existence detection)."""
    _, raw, processed = _project_paths(project_id)
    scp_xlsx = _find_xlsx(processed, raw, "scopus") or _find_xlsx(processed, raw, "scp")
    wos_xlsx = _find_xlsx(processed, raw, "wos")
    return scp_xlsx, wos_xlsx


def _load_inputs(
    project_id: str, ctx: JobContext | None = None
) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """Load the Scopus + WoS XLSX files (preparation should already have been done in auto_prepare).

    If there is only one source, it is returned and the other is None (the merge
    step handles this as a passthrough). Raises an error ONLY if neither exists —
    the user may want to prepare only Scopus data or only WoS data.
    """
    _, raw, processed = _project_paths(project_id)

    scp_xlsx, wos_xlsx = _input_paths(project_id)

    # If there is no prepared (processed) XLSX, merge does NOT automatically produce
    # one from raw CSV/TXT — that would bypass step 1 (Preparing). Instead, if raw
    # data exists but preparation has not been done, we return a clear error that
    # directs the user to step 1.
    if scp_xlsx is None and wos_xlsx is None:
        has_raw = raw.exists() and any(
            f.suffix.lower() in (".csv", ".txt", ".isi", ".xlsx")
            for f in raw.iterdir() if f.is_file()
        )
        # Raw data EXISTS but is not prepared → "prepare first"; no data at all → "upload first".
        raise HTTPException(409, "not_prepared" if has_raw else "no_source_data")

    scp_df = pd.read_excel(scp_xlsx) if scp_xlsx is not None else None
    wos_df = pd.read_excel(wos_xlsx) if wos_xlsx is not None else None
    if ctx:
        ctx.log(
            f"Sources — Scopus: {len(scp_df) if scp_df is not None else 'none'}, "
            f"WoS: {len(wos_df) if wos_df is not None else 'none'}"
        )
    return scp_df, wos_df


async def _run_single_source(
    ctx: JobContext, project_id: str,
    scp_df: pd.DataFrame | None, wos_df: pd.DataFrame | None,
) -> dict[str, Any]:
    """Single source (Scopus only or WoS only) — there is no second dataset to merge.
    Makes the single source the analysis dataset directly (no dedup), ready for filtering.
    """
    import asyncio
    df = scp_df if scp_df is not None else wos_df
    src = "Scopus" if scp_df is not None else "WoS"
    ctx.log(f"Single source ({src}) — no second dataset to merge, used directly (no dedup).")
    ctx.progress(0.3)

    analysis_id, adir = analyses.create_analysis(project_id, "single")
    ctx.log(f"Analysis folder: {analysis_id}")
    try:
        return await _finish_single_source(ctx, project_id, analysis_id, adir, df, scp_df, wos_df)
    except BaseException:
        # Includes a cancel (CancelledError is a BaseException): never leave an unfinished
        # analysis folder behind — it has no completed_at and must not become active.
        meta = analyses.get_analysis_meta(project_id, analysis_id) or {}
        if not meta.get("completed_at"):
            try:
                analyses.delete_analysis(project_id, analysis_id)
            except Exception:
                pass
        raise


async def _finish_single_source(
    ctx: JobContext, project_id: str, analysis_id: str, adir, df: pd.DataFrame,
    scp_df: pd.DataFrame | None, wos_df: pd.DataFrame | None,
) -> dict[str, Any]:
    import asyncio
    output_dataset = adir / "merged.parquet"
    await dataset_io.write_dataset_uncancellable(df, output_dataset)
    ctx.progress(0.7)

    scp_count = int(len(scp_df)) if scp_df is not None else 0
    wos_count = int(len(wos_df)) if wos_df is not None else 0
    statistic_xlsx = adir / "Statistic.xlsx"
    try:
        from services.smart_merger import _write_statistic_smart
        await asyncio.to_thread(
            _write_statistic_smart, int(len(df)), wos_count, scp_count, df, statistic_xlsx,
        )
    except Exception as e:
        ctx.log(f"Statistic.xlsx could not be generated: {e}")

    try:
        from services import filter_engine
        filter_engine._DF_CACHE.clear()
    except Exception:
        pass

    analyses.finalize_analysis(project_id, analysis_id)
    storage.touch_project(project_id)
    ctx.progress(1.0)
    ctx.log(f"Single source ready — {len(df)} records. You can proceed to the filtering step.")
    return {
        "method": "single",
        "analysis_id": analysis_id,
        "scopus_input": scp_count,
        "wos_input": wos_count,
        "merged_count": int(len(df)),
        "duplicates_removed": 0,
        "output_dataset": str(output_dataset.relative_to(storage.settings.storage_path)),
        "stats": {},
    }


class _ScaledJobContext:
    """Maps a sub-phase's 0-1 progress onto the [lo, hi] range of the parent job.

    Preparation (auto_prepare) brings the bar up to 18%; Smart Merge started at
    0.02 on its own scale — without wrapping, progress rewound from 12-18% back
    to 2% and the job looked frozen. log/cancelled are delegated as-is.
    """

    def __init__(self, ctx: JobContext, lo: float, hi: float):
        self._ctx, self._lo, self._hi = ctx, lo, hi

    def log(self, line: str) -> None:
        self._ctx.log(line)

    def progress(self, value: float) -> None:
        v = max(0.0, min(1.0, value))
        self._ctx.progress(self._lo + v * (self._hi - self._lo))

    @property
    def cancelled(self) -> bool:
        return self._ctx.cancelled


async def run_merge(ctx: JobContext, project_id: str) -> dict[str, Any]:
    """Merge job — a single algorithm: Smart Merge.

    If there is a single source (Scopus only or WoS only), it is a passthrough
    (no dedup); if there are two sources, the independent smart_merger pipeline
    runs — it opens its own analysis folder, finalizes it and returns its summary.
    """
    import asyncio

    ctx.log("Starting merge (Smart Merge)")
    ctx.progress(0.05)

    # Implicit preparation — convert raw CSV/TXT files into the consolidated XLSX (there is no separate Prepare step).
    # Skip-if-fresh: re-converts only if the raw file is newer than the processed one.
    ctx.log("Preparing sources (CSV/TXT → XLSX)")
    await asyncio.to_thread(converter.auto_prepare, project_id, ctx)

    # Single-source detection is done BY FILE EXISTENCE (without loading): when
    # two sources are present, loading the frames here as well kept a SECOND copy
    # of the corpus alive in memory while run_smart_merge made its own _load_inputs
    # call (hundreds of MB of unnecessary peak memory on large corpora).
    scp_xlsx, wos_xlsx = _input_paths(project_id)

    # Single source (or none): _load_inputs loads it; if neither exists, it raises the 409.
    if scp_xlsx is None or wos_xlsx is None:
        scp_df, wos_df = await asyncio.to_thread(_load_inputs, project_id, ctx)
        return await _run_single_source(ctx, project_id, scp_df, wos_df)

    # Both sources exist → Smart Merge (an independent pipeline; it opens its own
    # folder and loads the sources ITSELF — a single copy). Its progress is scaled
    # starting from 18%, where preparation ends, so that the bar does not rewind.
    from services import smart_merger
    return await smart_merger.run_smart_merge(_ScaledJobContext(ctx, 0.18, 1.0), project_id)


def list_merged(project_id: str) -> list[dict]:
    """List the files in the active analysis folder."""
    adir = analyses.get_active_analysis_dir(project_id)
    if adir is None:
        return []
    out = []
    for f in sorted(adir.iterdir()):
        if not f.is_file() or f.name == "meta.json":
            continue
        if f.name.endswith(".tmp~"):
            continue
        out.append({
            "name": f.name,
            "size": f.stat().st_size,
            "relative_path": str(f.relative_to(storage.settings.storage_path)),
        })
    return out


def merged_dataset_path(project_id: str) -> Path | None:
    """Find the main merged dataset (merged.parquet) for filter/export.

    Reads from the active analysis folder. Records / Filter / Quality /
    Disambiguation automatically see the active analysis's dataset.
    """
    return analyses.active_dataset_path(project_id)
