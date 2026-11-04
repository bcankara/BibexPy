"""API-based field enrichment for the active dataset.

The entry point `run_fill_all` reads the active dataset once and, for each DOI,
makes a single API call to fill all missing fields (fetch-once-fill-all), then
writes the result back to the active dataset. This keeps API calls to one per
DOI and surfaces the result in the dataset the dashboard reads.

These operations are long-running and are invoked through the job runner.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
from fastapi import HTTPException

from config import settings
from jobs.runner import JobContext
from services import analyses, dataset_io, filter_engine, merger, storage
from services.bibex_adapter import _suppress_stdio


def _active_path(project_id: str) -> Path:
    """Active analysis dataset — filter/quality/disambiguation read and write it."""
    src = merger.merged_dataset_path(project_id)
    if src is None:
        raise HTTPException(409, "no_merged_data")
    return src


def _snapshot(project_id: str, df: pd.DataFrame, tag: str) -> str:
    # The snapshot is written to the ACTIVE analysis folder (not the project root) — each
    # merge carries its own snapshots; a new analysis does not inherit the old ones.
    snaps = analyses.work_dir(project_id) / "snapshots"
    snaps.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:6]
    p = snaps / f"pre_{tag}_{stamp}.parquet"
    dataset_io.atomic_write_dataset(df, p)
    return str(p.relative_to(storage.settings.storage_path))


def _is_blank(v) -> bool:
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    return str(v).strip() in ("", "nan", "NaN")


# Fields tracked for the fill-rate report (same set as the dashboard)
_RATE_FIELDS = ("DI", "AB", "TI", "AU", "PY", "DE", "ID", "WC", "SC", "C1", "OI", "EM", "CR", "TC", "LA")


def _fill_rate(series) -> float:
    total = len(series)
    if not total:
        return 0.0
    filled = int(sum(0 if _is_blank(x) else 1 for x in series))
    return filled / total


def _mirror_wc_sc(df: pd.DataFrame) -> int:
    """WC<->SC mutual copy (deterministic): if one is filled, fill the blank one with it.

    Needs no DOI/API — immediately completes WC/SC in WoS records where only one of the two is missing.
    Returns the number of cells filled.
    """
    if "WC" not in df.columns or "SC" not in df.columns:
        return 0
    wc_blank = df["WC"].map(_is_blank)
    sc_blank = df["SC"].map(_is_blank)
    m1 = (~wc_blank) & sc_blank   # WC present, SC missing -> SC = WC
    m2 = (~sc_blank) & wc_blank   # SC present, WC missing -> WC = SC
    df.loc[m1, "SC"] = df.loc[m1, "WC"]
    df.loc[m2, "WC"] = df.loc[m2, "SC"]
    return int(m1.sum() + m2.sum())


async def _api_pass(ctx: JobContext, df: pd.DataFrame, *, lo: float = 0.05, hi: float = 0.95) -> dict[str, Any]:
    """Update df IN PLACE: one extract_metadata call per DOI, filling all blank fields."""
    from bibex_core.modules.api_utils import extract_metadata

    if "DI" not in df.columns:
        ctx.log("No DI (DOI) column — API pass skipped")
        return {"total": 0, "enriched": 0}

    with_doi = df[df["DI"].astype(str).str.strip().ne("") & df["DI"].astype(str).str.upper().ne("NAN")]
    total = len(with_doi)
    ctx.log(f"API: scanning {total} records with a DOI")

    creds = {
        "scopus_api_key": settings.scopus_api_key or None,
        "semantic_scholar_key": settings.semantic_scholar_api_key or None,
        "unpaywall_email": settings.unpaywall_email or None,
        "crossref_email": settings.crossref_email or None,
    }

    def _one(doi: str, current: dict):
        with _suppress_stdio():
            return extract_metadata(doi, current, **creds)

    enriched = 0
    for i, (idx, row) in enumerate(with_doi.iterrows()):
        if ctx.cancelled:
            ctx.log("Cancelled by user")
            break
        doi = str(row["DI"]).strip()
        current = {c: row[c] for c in df.columns}
        try:
            new_data = await asyncio.to_thread(_one, doi, current)
            updated = 0
            for k, v in new_data.items():
                if k in df.columns and _is_blank(df.at[idx, k]) and not _is_blank(v):
                    df.at[idx, k] = v
                    updated += 1
            if updated:
                enriched += 1
        except Exception as e:
            ctx.log(f"DOI {doi}: {e}")

        if (i + 1) % 10 == 0 or i == total - 1:
            ctx.progress(lo + (hi - lo) * ((i + 1) / max(1, total)))
            ctx.log(f"API: {i + 1}/{total} (enriched: {enriched})")

    return {"total": int(total), "enriched": int(enriched)}


async def _doi_pass(ctx: JobContext, df: pd.DataFrame, *, lo: float = 0.05, hi: float = 0.4) -> dict[str, Any]:
    """Fill missing DOIs by reverse lookup from title+year+author (CrossRef + OpenAlex, verified).

    Balanced threshold + automatic write; a record that is not confidently matched stays
    blank. df is updated IN PLACE.
    """
    from bibex_core.modules.api_utils import resolve_doi

    if "DI" not in df.columns or "TI" not in df.columns:
        return {"scanned": 0, "filled": 0}

    blank_di = df[df["DI"].map(_is_blank) & ~df["TI"].map(_is_blank)]
    total = len(blank_di)
    ctx.log(f"DOI lookup: {total} records without a DOI")
    if not total:
        ctx.progress(hi)
        return {"scanned": 0, "filled": 0}

    email = settings.crossref_email or None
    has_au, has_af, has_py = "AU" in df.columns, "AF" in df.columns, "PY" in df.columns
    filled = 0
    for i, (idx, row) in enumerate(blank_di.iterrows()):
        if ctx.cancelled:
            ctx.log("Cancelled by user")
            break
        title = row["TI"]
        authors = row["AU"] if has_au and not _is_blank(row["AU"]) else (row["AF"] if has_af else None)
        year = row["PY"] if has_py else None
        try:
            doi = await asyncio.to_thread(resolve_doi, title, authors, year, crossref_email=email)
        except Exception as e:
            ctx.log(f"DOI lookup error: {e}")
            doi = None
        if doi:
            df.at[idx, "DI"] = doi
            filled += 1
        if (i + 1) % 10 == 0 or i == total - 1:
            ctx.progress(lo + (hi - lo) * ((i + 1) / max(1, total)))
            ctx.log(f"DOI lookup: {i + 1}/{total} (found: {filled})")

    return {"scanned": int(total), "filled": int(filled)}


async def run_fill_all(ctx: JobContext, project_id: str) -> dict[str, Any]:
    """Unified 'Fill all missing' — API pass ONLY (one call per DOI, all blank fields).

    There is NO ML pass. df is read once, filled in place via the API, and written to the active dataset.
    Even if cancelled, the API gains obtained up to that point are preserved.
    """
    src = await asyncio.to_thread(_active_path, project_id)
    df = await asyncio.to_thread(dataset_io.read_dataset, src)
    ctx.log(f"Loaded: {len(df)} records")
    snap = _snapshot(project_id, df, "fill_all")
    ctx.log("Snapshot taken")
    ctx.progress(0.05)

    # Fill rate: rates BEFORE processing (for the report)
    track = [c for c in _RATE_FIELDS if c in df.columns]
    before = {c: _fill_rate(df[c]) for c in track}

    # 1) WC<->SC mutual copy — deterministic, needs no DOI, runs BEFORE the API (takes priority).
    mirrored = _mirror_wc_sc(df)
    if mirrored:
        ctx.log(f"WC<->SC mirror: {mirrored} field(s) filled")

    # 2-3) Reverse DOI lookup + API pass. df is updated IN PLACE; even on cancellation
    #    (CancelledError or ctx.cancelled) the gains obtained up to that point are
    #    PRESERVED — we wrap the passes and write to the active dataset in the finally block.
    doi_stats = {"scanned": 0, "filled": 0}
    api_stats = {"total": 0, "enriched": 0}
    addr_stats = {"rows": 0, "addr": 0, "dois": 0}
    cancelled = False
    try:
        doi_stats = await _doi_pass(ctx, df, lo=0.05, hi=0.4)
        api_stats = await _api_pass(ctx, df, lo=0.4, hi=0.9)
        # Address completion: for addresses that have an institution but a MISSING COUNTRY,
        # deterministically add the OpenAlex country (API, no LLM, free — transparent to
        # the user, part of Fill).
        email = settings.crossref_email or settings.unpaywall_email or None
        addr_stats = await _complete_addresses_pass(ctx, df, email, lo=0.9, hi=0.97)
    except asyncio.CancelledError:
        cancelled = True
    finally:
        _mirror_wc_sc(df)  # if the API filled only one of the fields, equalize WC/SC
        # Write atomically — so the active dataset is never left half-written on
        # cancellation/interruption or during a concurrent poll read
        # (see dataset_io.atomic_write_dataset)
        # Re-resolve the active path — during a long-running job the migration may have
        # moved from .xlsx to .parquet; writing to the old src creates a zombie file.
        dst = await asyncio.to_thread(_active_path, project_id) or src
        await asyncio.to_thread(dataset_io.atomic_write_dataset, df, dst)
        filter_engine._DF_CACHE.clear()
        storage.touch_project(project_id)
    if cancelled:
        raise asyncio.CancelledError()

    # Fill rate: rates AFTER processing + per-field before/after
    after = {c: _fill_rate(df[c]) for c in track}
    per_field_fill = {c: {"before": round(before[c], 4), "after": round(after[c], 4)} for c in track}
    overall_before = round(sum(before.values()) / len(before), 4) if before else 0.0
    overall_after = round(sum(after.values()) / len(after), 4) if after else 0.0
    ctx.progress(1.0)
    ctx.log(
        f"Done — DOIs found: {doi_stats['filled']}; API: {api_stats['enriched']} records; "
        f"addresses+country: {addr_stats['rows']}; "
        f"fill {round(overall_before*100,1)}% -> {round(overall_after*100,1)}%"
    )

    return {
        "method": "fill_all",
        # Which analysis it belongs to — so the report is shown only in the ACTIVE analysis
        # (a new merge must not carry over the old enrichment report).
        "analysis_id": analyses.get_active_analysis_id(project_id),
        "total": int(len(df)),
        "enriched": int(api_stats["enriched"]),
        "api": api_stats,
        "doi": doi_stats,
        "addresses": addr_stats,
        "fill_rate_before": overall_before,
        "fill_rate_after": overall_after,
        "per_field_fill": per_field_fill,
        "snapshot": snap,
    }


async def _complete_addresses_pass(ctx: JobContext, df: pd.DataFrame, email: str | None,
                                   lo: float = 0.95, hi: float = 0.99) -> dict[str, int]:
    """Address completion PASS (API ONLY, NO LLM): DETERMINISTICALLY adds the country, taken
    from the OpenAlex country_code, to C1 addresses that have an institution but a MISSING
    COUNTRY.

    df is updated IN PLACE (NO snapshot/write of its own — run_fill_all handles that). An
    existing country is NOT OVERWRITTEN; institution/city are preserved. Distinction: if
    parse_c1_address.country is None the address has no country → add it; if it is filled
    (e.g. 'India') skip it. Country source: if the institution name matches an OpenAlex
    institution, that country; otherwise, for a single-country paper, that country."""
    from bibex_core.modules.c1_utils import split_c1_addresses, parse_c1_address, append_country_to_c1
    from services.disambiguation.orcid import fetch_affiliations_for_doi
    from services.disambiguation.similarity import normalize_name

    cols = [c for c in ("C1", "C1raw") if c in df.columns]
    if not cols or "DI" not in df.columns:
        return {"rows": 0, "addr": 0, "dois": 0}
    main_col = "C1" if "C1" in df.columns else cols[0]
    rows_changed = 0
    addr_filled = 0
    seen_doi: set[str] = set()
    n = len(df)
    for i, idx in enumerate(df.index):
        if ctx.cancelled:
            break
        doi = df.at[idx, "DI"]
        if doi is None or not str(doi).strip() or str(doi).strip().lower() == "nan":
            continue
        # Address(es) with a missing country: those where parse_c1_address.country is None
        # (the last component is not a country).
        tokens: list[str] = []
        for addr in split_c1_addresses(df.at[idx, main_col]):
            if parse_c1_address(addr)["country"] is None:
                parts = [p.strip() for p in addr.split(",") if p.strip()]
                if parts:
                    tokens.append(parts[-1])
        if not tokens:
            continue
        d = str(doi).strip()
        affs = await asyncio.to_thread(fetch_affiliations_for_doi, d, email)
        seen_doi.add(d)
        if not affs:
            continue
        distinct = {c for (_n, c) in affs if c}
        single = next(iter(distinct)) if len(distinct) == 1 else None
        append_map: dict[str, str] = {}
        for tok in tokens:
            ntok = normalize_name(tok)
            matched = None
            if ntok:
                for (iname, ic) in affs:
                    if ic and normalize_name(iname) == ntok:
                        matched = ic
                        break
            country = matched or single
            if country:
                append_map[tok.lower()] = country
        if not append_map:
            continue
        changed = False
        for col in cols:
            old = "" if pd.isna(df.at[idx, col]) else str(df.at[idx, col])
            new = append_country_to_c1(old, append_map)
            if new != old:
                df.at[idx, col] = new
                changed = True
        if changed:
            rows_changed += 1
            addr_filled += len(append_map)
        if (i % 25) == 0:
            ctx.progress(min(hi, lo + (hi - lo) * (i / max(1, n))))
    return {"rows": rows_changed, "addr": addr_filled, "dois": len(seen_doi)}


# ── Backward compatibility: standalone API endpoint (the UI now uses run_fill_all; there is no ML) ──

async def run_api_enrichment(ctx: JobContext, project_id: str, sources: list[str] | None = None) -> dict[str, Any]:
    """API pass only — writes to the active dataset."""
    src = await asyncio.to_thread(_active_path, project_id)
    df = await asyncio.to_thread(dataset_io.read_dataset, src)
    ctx.log(f"Loaded: {len(df)} records")
    snap = _snapshot(project_id, df, "api")
    ctx.progress(0.05)
    stats = await _api_pass(ctx, df, lo=0.05, hi=0.95)
    # Re-resolve the active path — during the job the migration may have moved from
    # .xlsx to .parquet; writing to the old src creates a zombie file.
    dst = await asyncio.to_thread(_active_path, project_id) or src
    await asyncio.to_thread(dataset_io.atomic_write_dataset, df, dst)
    filter_engine._DF_CACHE.clear()
    storage.touch_project(project_id)
    ctx.progress(1.0)
    return {"method": "api", "total": stats["total"], "enriched": stats["enriched"], "snapshot": snap}
