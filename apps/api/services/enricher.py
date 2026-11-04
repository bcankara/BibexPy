"""API-based field enrichment for the active dataset.

The entry point `run_fill_all` reads the active dataset once and, for each DOI,
makes a single API call to fill all missing fields (fetch-once-fill-all), then
writes the result back to the active dataset. This keeps API calls to one per
DOI and surfaces the result in the dataset the dashboard reads.

These operations are long-running and are invoked through the job runner.
"""

from __future__ import annotations

import asyncio
import csv
import os
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


def _snapshot(project_id: str, df: pd.DataFrame, tag: str, base: Path | None = None) -> str:
    # The snapshot is written to the analysis folder (not the project root) — each merge
    # carries its own snapshots; a new analysis does not inherit the old ones. `base` pins
    # the folder a long-running job started in; default = the currently active analysis.
    snaps = (base if base is not None else analyses.work_dir(project_id)) / "snapshots"
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


class _Cadence:
    """Throttles status notifications to ~2 per second (every `every` items or `seconds`)."""

    def __init__(self, every: int = 5, seconds: float = 1.0):
        self.every, self.seconds, self._last = every, seconds, 0.0

    def due(self, i: int, last: bool = False) -> bool:
        now = time.monotonic()
        if last or i % self.every == 0 or now - self._last >= self.seconds:
            self._last = now
            return True
        return False


async def _api_pass(ctx: JobContext, df: pd.DataFrame, *, lo: float = 0.05, hi: float = 0.95,
                    fresh: dict[Any, str] | None = None,
                    verdicts: dict[Any, str] | None = None) -> dict[str, Any]:
    """Update df IN PLACE: one extract_metadata call per DOI, filling all blank fields.

    `fresh` maps the index of every row whose DOI was written by THIS run's reverse lookup to
    its record title. For those rows the title returned by the DOI's metadata is compared
    with the record title (second insurance against a wrong DOI): on a mismatch nothing is
    filled from that response and the DOI is reverted to blank. The outcome per row is
    reported in `verdicts` ('suspect' | 'unverified').
    """
    from bibex_core.modules.api_utils import extract_metadata, same_work_titles

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
        # All OpenAlex calls here are singleton /works/doi: lookups (free); the key only
        # lifts the per-IP limits.
        "openalex_api_key": settings.openalex_api_key or None,
    }

    def _one(doi: str, current: dict):
        with _suppress_stdio():
            return extract_metadata(doi, current, **creds)

    cadence = _Cadence(every=10)
    enriched = 0
    for i, (idx, row) in enumerate(with_doi.iterrows()):
        if ctx.cancelled:
            ctx.log("Cancelled by user")
            break
        doi = str(row["DI"]).strip()
        current = {c: row[c] for c in df.columns}
        is_fresh = bool(fresh) and idx in fresh
        if is_fresh and "TI" in current:
            current["TI"] = ""   # let the DOI's own title come back so it can be compared
        try:
            new_data = await asyncio.to_thread(_one, doi, current)
            suspect = False
            if is_fresh:
                api_title = new_data.get("TI")
                if _is_blank(api_title):
                    if verdicts is not None:
                        verdicts[idx] = "unverified"
                else:
                    ok, _score, why = same_work_titles(fresh[idx], api_title)
                    if not ok:
                        suspect = True
                        df.at[idx, "DI"] = ""   # revert: the DOI points to a different work
                        if verdicts is not None:
                            verdicts[idx] = "suspect"
                        ctx.log(f"DOI {doi}: returned title does not match the record ({why}) — DOI removed")
            updated = 0
            for k, v in ({} if suspect else new_data).items():
                if k in df.columns and _is_blank(df.at[idx, k]) and not _is_blank(v):
                    df.at[idx, k] = v
                    updated += 1
            if updated:
                enriched += 1
        except Exception as e:
            ctx.log(f"DOI {doi}: {e}")

        if cadence.due(i + 1, last=(i == total - 1)):
            ctx.progress(lo + (hi - lo) * ((i + 1) / max(1, total)))
            ctx.log(f"API: {i + 1}/{total} (enriched: {enriched})",
                    key="jobs.status.apiPass",
                    params={"done": i + 1, "total": int(total), "enriched": enriched})

    return {"total": int(total), "enriched": int(enriched)}


# Per-record fields handed to the reverse lookup.
_LOOKUP_COLS = ("TI", "AU", "AF", "PY", "SO", "JI", "J9", "SN", "EI", "VL", "BP", "DT")

# Outcomes of the reverse lookup that are reported as "skipped" (a DOI was NOT written).
_DOI_SKIPPED = ("ambiguous", "preprint_only", "duplicate", "type_rejected", "no_match", "api_error")

# Columns of the per-record provenance CSV (doi_lookup_<stamp>.csv).
_PROVENANCE_COLUMNS = (
    "UID", "record_title", "record_year", "record_first_author", "outcome", "written_doi",
    "source", "cand_title", "cand_type", "cand_container", "score", "reason", "preprint_doi",
    "warning", "duplicate_of",
)


def _new_doi_stats() -> dict[str, Any]:
    """Result shape of the DOI pass (CONTRACT section 3) + private working data.

    Keys starting with '_' are internal (provenance rows, rows written by this run) and are
    removed by `_public_doi_stats` before the result leaves the job.
    """
    return {
        "scanned": 0,
        "filled": 0,
        "skipped": {**{k: 0 for k in _DOI_SKIPPED}, "suspect": 0},
        "sources_exhausted": [],
        "provenance": None,
        "_rows": [],       # provenance rows, in scan order
        "_fresh": {},      # df index -> record title, for DOIs written by this run
        "_row_of": {},     # df index -> position of its provenance row in _rows
        "_csv": None,      # Path of the provenance CSV
    }


def _public_doi_stats(stats: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in stats.items() if not k.startswith("_")}


def _uid_of(df: pd.DataFrame, idx) -> str:
    if "UID" in df.columns and not _is_blank(df.at[idx, "UID"]):
        return str(df.at[idx, "UID"]).strip()
    return f"row {idx}"


def _csv_safe(v: Any) -> str:
    """Neutralize spreadsheet formula injection (titles come from external APIs)."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _write_provenance(path: Path, rows: list[dict[str, Any]]) -> bool:
    """Write the provenance CSV atomically (temp file + os.replace), UTF-8 with BOM for Excel."""
    tmp = path.with_name(f"{path.name}.{uuid4().hex[:8]}.tmp~")
    try:
        with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(_PROVENANCE_COLUMNS), extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: _csv_safe(r.get(k, "")) for k in _PROVENANCE_COLUMNS})
        os.replace(tmp, path)
        return True
    except OSError:
        return False
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _apply_verdicts(stats: dict[str, Any], verdicts: dict[Any, str]) -> None:
    """Fold the API pass' second-insurance verdicts into the DOI stats and provenance.

    'suspect' rows had their DOI reverted: they no longer count as filled, are tallied under
    skipped.no_match (+ skipped.suspect) and the provenance row is rewritten. Idempotent.
    """
    rows, applied = stats["_rows"], stats.setdefault("_applied", set())
    changed = False
    for idx, verdict in verdicts.items():
        if idx in applied:
            continue
        applied.add(idx)
        pos = stats["_row_of"].get(idx)
        r = rows[pos] if pos is not None and pos < len(rows) else None
        if r is not None and r["outcome"] == "filled":
            if verdict == "suspect":
                r["outcome"] = "suspect_after_fetch"
                r["warning"] = "the title returned by the DOI does not match the record title; DOI removed"
                r["written_doi"] = ""
                stats["filled"] -= 1
                stats["skipped"]["no_match"] += 1
                stats["skipped"]["suspect"] += 1
            else:
                r["warning"] = "not re-verified after fetch (no title returned)"
            changed = True
        if verdict == "suspect":
            stats["_fresh"].pop(idx, None)
    path = stats.get("_csv")
    if changed and path is not None:
        _write_provenance(path, rows)


def _rel_to_storage(p: Path) -> str:
    base = Path(storage.settings.storage_path)
    for a, b in ((p, base), (p.resolve(), base.resolve())):
        try:
            return a.relative_to(b).as_posix()
        except ValueError:
            continue
    return p.name


async def _doi_pass(ctx: JobContext, df: pd.DataFrame, *, lo: float = 0.05, hi: float = 0.4,
                    out_dir: Path | None = None, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fill missing DOIs by precision-first reverse lookup (Crossref, OpenAlex only as fallback).

    A record that is not matched with confidence stays blank: the lookup abstains on
    ambiguous results, preprint-only matches, wrong publication types and DOIs that already
    sit on another row. df is updated IN PLACE. `stats` (shape of `_new_doi_stats`) is
    updated as the pass goes on, so a cancelled run still reports what it did. One
    provenance row per scanned record goes to `<out_dir>/doi_lookup_<stamp>.csv`.
    """
    from bibex_core.modules import api_utils

    stats = stats if stats is not None else _new_doi_stats()
    if "DI" not in df.columns or "TI" not in df.columns:
        return stats

    blank_di = df[df["DI"].map(_is_blank) & ~df["TI"].map(_is_blank)]
    total = len(blank_di)
    ctx.log(f"DOI lookup: {total} records without a DOI")
    if not total:
        ctx.progress(hi)
        return stats

    # pandas 3 safety: a fully empty DI column can be float64/str dtype, and assigning a
    # string into it would raise.
    if df["DI"].dtype != object:
        df["DI"] = df["DI"].astype(object)

    # Every DOI already in the dataset (any row, canonical comparison form) -> holder UID.
    existing: dict[str, str] = {}
    for idx, v in df["DI"].items():
        if not _is_blank(v):
            key = api_utils.doi_key(v)
            if key:
                existing.setdefault(key, _uid_of(df, idx))

    email = settings.crossref_email or settings.unpaywall_email or None
    openalex_key = settings.openalex_api_key or None
    state = api_utils.DoiLookupState()
    skipped = stats["skipped"]
    rows: list[dict[str, Any]] = stats["_rows"]
    csv_path = None
    if out_dir is not None:
        csv_path = Path(out_dir) / f"doi_lookup_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        stats["_csv"] = csv_path
    cadence = _Cadence(every=5)
    try:
        for i, (idx, row) in enumerate(blank_di.iterrows()):
            if ctx.cancelled:
                ctx.log("Cancelled by user")
                break
            uid = _uid_of(df, idx)
            rec = {c: row[c] for c in _LOOKUP_COLS if c in df.columns}
            rec["UID"] = uid
            try:
                dec = await asyncio.to_thread(
                    api_utils.lookup_doi, rec, existing=existing, email=email,
                    openalex_key=openalex_key, state=state,
                )
            except Exception as e:
                ctx.log(f"DOI lookup error: {e}")
                dec = api_utils.DoiDecision(None, "api_error", reason=f"lookup error: {e}")
            stats["scanned"] += 1
            if dec.outcome == "filled" and dec.doi:
                df.at[idx, "DI"] = dec.doi
                existing[api_utils.doi_key(dec.doi)] = uid
                stats["filled"] += 1
                stats["_fresh"][idx] = str(row["TI"])
                stats["_row_of"][idx] = len(rows)   # the row appended below
            else:
                skipped[dec.outcome if dec.outcome in skipped else "no_match"] += 1
            stats["sources_exhausted"] = sorted(state.exhausted)

            au_src = rec.get("AU") if not _is_blank(rec.get("AU")) else rec.get("AF")
            first_author = "" if _is_blank(au_src) else str(au_src)
            rows.append({
                "UID": uid,
                "record_title": str(row["TI"]),
                "record_year": "" if _is_blank(rec.get("PY")) else str(rec.get("PY")),
                "record_first_author": first_author.split(";")[0].strip(),
                "outcome": dec.outcome,
                "written_doi": dec.doi or "",
                "source": dec.source,
                "cand_title": dec.cand_title,
                "cand_type": dec.cand_type,
                "cand_container": dec.cand_container,
                "score": dec.score if dec.source else "",
                "reason": dec.reason,
                "preprint_doi": dec.preprint_doi or "",
                "warning": dec.warning,
                "duplicate_of": dec.duplicate_of or "",
            })

            if cadence.due(i + 1, last=(i == total - 1)):
                ctx.progress(lo + (hi - lo) * ((i + 1) / max(1, total)))
                ctx.log(f"DOI lookup: {i + 1}/{total} (found: {stats['filled']})",
                        key="jobs.status.doiLookup",
                        params={"done": i + 1, "total": int(total), "found": stats["filled"]})
    finally:
        if csv_path is not None and rows and _write_provenance(csv_path, rows):
            stats["provenance"] = _rel_to_storage(csv_path)
        if state.exhausted:
            ctx.log("Sources exhausted this run: " + ", ".join(sorted(state.exhausted)))

    return stats


async def run_fill_all(ctx: JobContext, project_id: str) -> dict[str, Any]:
    """Unified 'Fill all missing' — API pass ONLY (one call per DOI, all blank fields).

    There is NO ML pass. df is read once, filled in place via the API, and written back to the
    dataset the job started with. Even if cancelled, the API gains obtained up to that point
    are preserved.
    """
    from bibex_core.modules import api_utils

    # Pin the target at job start: the final write goes back to THIS dataset even if the
    # user switches the active analysis while the job runs.
    src = await asyncio.to_thread(_active_path, project_id)
    out_dir = src.parent
    # The analysis id comes from the folder the dataset was resolved in (the folder name IS the
    # id): re-reading the active pointer here could already see a different analysis.
    analysis_id = (out_dir.name if analyses.analysis_dir(project_id, out_dir.name) == out_dir
                   else analyses.get_active_analysis_id(project_id))
    api_utils.set_openalex_api_key(settings.openalex_api_key or None)
    df = await asyncio.to_thread(dataset_io.read_dataset, src)
    ctx.log(f"Loaded: {len(df)} records", key="jobs.status.loading", params={"n": int(len(df))})
    snap = _snapshot(project_id, df, "fill_all", base=out_dir)
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
    doi_stats = _new_doi_stats()
    api_stats = {"total": 0, "enriched": 0}
    addr_stats = {"rows": 0, "addr": 0, "dois": 0}
    verdicts: dict[Any, str] = {}
    cancelled = False
    gone = False
    try:
        await _doi_pass(ctx, df, lo=0.05, hi=0.4, out_dir=out_dir, stats=doi_stats)
        api_stats = await _api_pass(ctx, df, lo=0.4, hi=0.9,
                                    fresh=doi_stats["_fresh"], verdicts=verdicts)
        _apply_verdicts(doi_stats, verdicts)
        # Address completion: for addresses that have an institution but a MISSING COUNTRY,
        # deterministically add the OpenAlex country (API, no LLM, free — transparent to
        # the user, part of Fill).
        email = settings.crossref_email or settings.unpaywall_email or None
        addr_stats = await _complete_addresses_pass(ctx, df, email, lo=0.9, hi=0.97)
    except asyncio.CancelledError:
        cancelled = True
    finally:
        _apply_verdicts(doi_stats, verdicts)   # no-op when already applied
        _mirror_wc_sc(df)  # if the API filled only one of the fields, equalize WC/SC
        ctx.log("Writing results", key="jobs.status.writing")
        # Write atomically — so the dataset is never left half-written on
        # cancellation/interruption or during a concurrent poll read
        # (see dataset_io.atomic_write_dataset). The write goes back to the dataset the job
        # STARTED with (`src`), never to a re-resolved "active" one: if the user switched
        # analyses meanwhile, the enrichment still belongs to the analysis it was run on.
        gone = not await asyncio.to_thread(out_dir.exists)
        if not gone:
            try:
                # Uncancellable: the task (and the exclusivity lock) must not end while the
                # write thread is still running.
                await dataset_io.write_dataset_uncancellable(df, src)
            finally:
                filter_engine._DF_CACHE.clear()
                storage.touch_project(project_id)
    if gone:
        # The pre-run snapshot is left untouched.
        raise RuntimeError(f"analysis_gone: analysis folder no longer exists ({out_dir.name})")
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
        f"fill {round(overall_before*100,1)}% -> {round(overall_after*100,1)}%",
        key="jobs.status.done",
        params={"doi": int(doi_stats["filled"]), "enriched": int(api_stats["enriched"])},
    )

    return {
        "method": "fill_all",
        # Which analysis it belongs to — so the report is shown only in the ACTIVE analysis
        # (a new merge must not carry over the old enrichment report). This is the analysis
        # the job STARTED on, even if the user switched meanwhile.
        "analysis_id": analysis_id,
        "total": int(len(df)),
        "enriched": int(api_stats["enriched"]),
        "api": api_stats,
        "doi": _public_doi_stats(doi_stats),
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
    cadence = _Cadence(every=25)
    for i, idx in enumerate(df.index):
        if ctx.cancelled:
            break
        if cadence.due(i + 1, last=(i == n - 1)):
            ctx.progress(min(hi, lo + (hi - lo) * (i / max(1, n))))
            ctx.log(f"Addresses: {i + 1}/{n}", key="jobs.status.addresses",
                    params={"done": i + 1, "total": int(n)})
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
    return {"rows": rows_changed, "addr": addr_filled, "dois": len(seen_doi)}


# ── Backward compatibility: standalone API endpoint (the UI now uses run_fill_all; there is no ML) ──

async def run_api_enrichment(ctx: JobContext, project_id: str, sources: list[str] | None = None) -> dict[str, Any]:
    """API pass only — writes back to the dataset the job started with."""
    from bibex_core.modules import api_utils

    src = await asyncio.to_thread(_active_path, project_id)
    out_dir = src.parent
    api_utils.set_openalex_api_key(settings.openalex_api_key or None)
    df = await asyncio.to_thread(dataset_io.read_dataset, src)
    ctx.log(f"Loaded: {len(df)} records", key="jobs.status.loading", params={"n": int(len(df))})
    snap = _snapshot(project_id, df, "api", base=out_dir)
    ctx.progress(0.05)
    stats = await _api_pass(ctx, df, lo=0.05, hi=0.95)
    if not await asyncio.to_thread(out_dir.exists):
        raise RuntimeError(f"analysis_gone: analysis folder no longer exists ({out_dir.name})")
    ctx.log("Writing results", key="jobs.status.writing")
    try:
        await dataset_io.write_dataset_uncancellable(df, src)
    finally:
        filter_engine._DF_CACHE.clear()
        storage.touch_project(project_id)
    ctx.progress(1.0)
    return {"method": "api", "total": stats["total"], "enriched": stats["enriched"], "snapshot": snap}
