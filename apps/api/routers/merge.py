import json
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from jobs.runner import job_runner
from services import analyses, dataset_io, merger, storage

router = APIRouter(prefix="/projects/{project_id}/merge", tags=["merge"])


class MergeRequest(BaseModel):
    # Single algorithm: Smart Merge. The field is kept for backward compatibility but ignored.
    method: str = "smart"


@router.post("")
async def start_merge(project_id: str, payload: MergeRequest | None = None):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    # A merge writes a new analysis and flips the active pointer: it must not overlap with
    # an enrichment/disambiguation job (and vice versa, because the merge is exclusive too).
    job_runner.assert_idle(project_id)
    # Single algorithm: Smart Merge — payload.method is now ignored.
    # Readiness check — BEFORE submitting the job; if there is no prepared XLSX, 409 (the UI
    # sends the user back to step 1). This way the merge never silently uses raw CSV/TXT.
    merger.check_ready_to_merge(project_id)

    async def worker(ctx):
        return await merger.run_merge(ctx, project_id)

    job = job_runner.submit(
        project_id=project_id,
        kind="merge",
        title="Merge (Smart Merge)",
        title_key="audit.titles.merge",
        title_params={"method": "smart"},
        worker=worker,
        exclusive=True,
    )
    return {"job_id": job.id}


@router.get("/results")
def list_merged(project_id: str):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    return merger.list_merged(project_id)


# Field codes → descriptive English label
FIELD_LABELS = {
    "TI": "Title", "AU": "Authors (short)", "AF": "Authors (full)",
    "SO": "Journal/Source", "JI": "Journal (ISO)", "J9": "Journal (short)",
    "PY": "Year", "TC": "Citation count", "DT": "Document type", "LA": "Language",
    "DI": "DOI", "AB": "Abstract", "DE": "Keywords", "ID": "Keywords Plus",
    "C1": "Addresses", "RP": "Corresponding author", "EM": "Email",
    "CR": "References", "NR": "Reference count", "PG": "Pages", "PU": "Publisher",
    "WC": "WoS Category", "SC": "Subject Category", "DB": "Source DB",
    "UT": "WoS ID", "PM": "PubMed ID", "VL": "Volume", "IS": "Issue",
    "BP": "Start page", "EP": "End page", "OA": "Open Access",
    "SR": "Source-Year ref",
}


def _merge_is_stale(project_id: str, meta: dict[str, Any] | None) -> bool:
    """True if raw data was added/changed AFTER the active merge.

    Since preparation is now done implicitly inside the merge, when the user
    adds a new raw file the mtime of raw/*.{csv,txt,isi} exceeds the merge
    time → the active merge relies on old data. The UI flags this as "Stale
    Merge" and directs the user to run Smart Merge again.
    """
    if not meta:
        return False
    merge_ts = meta.get("completed_at") or meta.get("created_at")
    if not merge_ts:
        return False
    try:
        raw = storage.project_dir(project_id) / "raw"
        if not raw.exists():
            return False
        newest = 0.0
        for pat in ("*.csv", "*.txt", "*.isi"):
            for f in raw.glob(pat):
                try:
                    newest = max(newest, f.stat().st_mtime)
                except OSError:
                    continue
        # 2 s tolerance: avoid a false positive caused by the small gap
        # between the merge's completed_at and the last upload's mtime.
        return bool(newest and newest > float(merge_ts) + 2.0)
    except Exception:
        return False


@router.get("/summary")
def merge_summary(project_id: str) -> dict[str, Any]:
    """Detailed statistics of the active analysis.

    has_merge = whether an active analysis folder exists. has_merge=True is
    returned whether or not Statistic.xlsx exists; without Statistic, only
    `general` stays empty and the `files` list is still populated.
    """
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")

    # Active analysis folder
    merged_dir = analyses.get_active_analysis_dir(project_id)
    if merged_dir is None or not merged_dir.exists():
        return {"has_merge": False}

    out: dict[str, Any] = {"has_merge": True}
    # Attach the analysis meta (independent of Statistic — always returned if an analysis exists)
    meta = analyses.get_analysis_meta(project_id, merged_dir.name)
    if meta:
        out["analysis"] = {
            "id": meta.get("id"),
            "label": meta.get("label"),
            "label_auto": bool(meta.get("label_auto", False)),
            "method": meta.get("method"),
            "created_at": meta.get("created_at"),
            "completed_at": meta.get("completed_at"),
            "is_active": meta.get("is_active"),
        }

    # Staleness: if the processed data was re-prepared after this merge
    # (new file + prepare again), the active merge relies on old data.
    out["stale"] = _merge_is_stale(project_id, meta)

    # Statistic.xlsx is preferred; Statistic_Smart is also tried for backward compatibility
    stats_xlsx = merged_dir / "Statistic.xlsx"
    if not stats_xlsx.exists():
        stats_xlsx_smart = merged_dir / "Statistic_Smart.xlsx"
        if stats_xlsx_smart.exists():
            stats_xlsx = stats_xlsx_smart

    # 1) General statistics — read from Statistic.xlsx first; if missing, compute from merged.xlsx + lost
    if stats_xlsx.exists():
        try:
            gen = pd.read_excel(stats_xlsx, sheet_name="General Stats")
            if len(gen) > 0:
                row = gen.iloc[0]
                out["general"] = {
                    "total_records": int(row.get("Total Records", 0)),
                    "wos_records": int(row.get("WoS Records", 0)),
                    "scopus_records": int(row.get("Scopus Records", 0)),
                    "merged_columns": int(row.get("Merged Columns", 0)),
                    "common_columns": int(row.get("Common Columns", 0)),
                }
                wos = out["general"]["wos_records"]
                scopus = out["general"]["scopus_records"]
                total_in = wos + scopus
                merged = out["general"]["total_records"]
                out["general"]["duplicates_removed"] = max(0, total_in - merged)
                out["general"]["total_input"] = total_in
                out["general"]["dedup_rate"] = round((total_in - merged) / total_in, 4) if total_in else 0
        except Exception:
            pass

    # Fallback: Statistic.xlsx missing / unreadable → compute from merged.xlsx
    if "general" not in out:
        try:
            from services import merger
            dataset = merger.merged_dataset_path(project_id)
            if dataset and dataset.exists():
                df = dataset_io.read_dataset(dataset)
                total_records = int(len(df))
                merged_columns = int(len(df.columns))
                out["general"] = {
                    "total_records": total_records,
                    "wos_records": 0,
                    "scopus_records": 0,
                    "merged_columns": merged_columns,
                    "common_columns": 0,
                    "duplicates_removed": 0,
                    "total_input": total_records,
                    "dedup_rate": 0,
                }
        except Exception:
            pass

    # 2) Per-field statistics
    if stats_xlsx.exists():
        try:
            fs = pd.read_excel(stats_xlsx, sheet_name="Field Stats")
            field_col = fs.columns[0]
            fields = []
            total = out.get("general", {}).get("total_records", 0)
            for _, r in fs.iterrows():
                code = str(r[field_col]).strip()
                if not code or code.lower() == "nan":
                    continue
                missing = int(r.get("Missing Count", 0))
                pct = float(r.get("Missing %", 0))
                status = str(r.get("Status", ""))
                fields.append({
                    "field": code,
                    "label": FIELD_LABELS.get(code, code),
                    "total": total,
                    "missing": missing,
                    "filled": total - missing,
                    "missing_pct": round(pct, 2),
                    "fill_rate": round((total - missing) / total, 4) if total else 0,
                    "status": status,
                })
            out["fields"] = fields
        except Exception:
            out["fields"] = []
    else:
        out["fields"] = []

    # 3) Files — main output + side files
    files = []
    lost_wos = 0
    lost_scopus = 0
    for f in sorted(merged_dir.iterdir()):
        if not f.is_file() or f.name == "meta.json":
            continue
        # Leftover of an interrupted atomic write — do not show it to the user as a file.
        if f.name.endswith(".tmp~"):
            continue
        kind = "other"
        name_lower = f.name.lower()
        suffix = f.suffix.lower()
        if (name_lower in ("merged.parquet", "merged.xlsx")
                or ("merged" in name_lower and suffix in (".xlsx", ".parquet"))):
            kind = "merged_dataset"
        elif name_lower.startswith("lost_wos"):
            kind = "lost_wos"
            try:
                lost_wos = int(len(pd.read_excel(f)))
            except Exception:
                pass
        elif name_lower.startswith("lost_scopus"):
            kind = "lost_scopus"
            try:
                lost_scopus = int(len(pd.read_excel(f)))
            except Exception:
                pass
        elif name_lower.startswith("statistic"):
            kind = "statistics"
        elif name_lower == "match_audit.xlsx":
            kind = "match_audit"
        elif name_lower == "conflict_log.xlsx":
            kind = "conflict_log"
        elif name_lower == "borderline_queue.xlsx":
            kind = "borderline_queue"
        files.append({
            "name": f.name,
            "size": f.stat().st_size,
            "mtime": f.stat().st_mtime,
            "kind": kind,
            "relative_path": str(f.relative_to(storage.settings.storage_path)),
        })
    out["files"] = files
    out["lost_wos_count"] = lost_wos
    out["lost_scopus_count"] = lost_scopus

    # 4) Method — single algorithm, Smart Merge. Use the one in meta.json if present
    #    ("single" for a single source), otherwise assume smart.
    method = (meta.get("method") if meta else None) or "smart"
    out["method"] = method

    # 5) Smart-specific statistics
    if method == "smart":
        # match_audit.xlsx → stage distribution
        audit_xlsx = merged_dir / "match_audit.xlsx"
        if audit_xlsx.exists():
            try:
                audit_df = pd.read_excel(audit_xlsx)
                if "stage_label" in audit_df.columns:
                    stage_counts = audit_df["stage_label"].value_counts().to_dict()
                    out["match_stages"] = {str(k): int(v) for k, v in stage_counts.items()}
            except Exception:
                out["match_stages"] = {}

        # conflict_log.xlsx → conflict count
        conflict_xlsx = merged_dir / "conflict_log.xlsx"
        if conflict_xlsx.exists():
            try:
                cdf = pd.read_excel(conflict_xlsx)
                out["conflict_count"] = int(len(cdf))
                if "chosen_source" in cdf.columns:
                    src_dist = cdf["chosen_source"].value_counts().to_dict()
                    out["field_source_distribution"] = {str(k): int(v) for k, v in src_dist.items()}
            except Exception:
                out["conflict_count"] = 0

        # pending count from the borderline state
        state_path = merged_dir / "borderline_state.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                out["borderline_pending"] = sum(1 for v in state.values() if v.get("status") == "pending")
                out["borderline_total"] = len(state)
            except Exception:
                out["borderline_pending"] = 0
                out["borderline_total"] = 0
        else:
            out["borderline_pending"] = 0
            out["borderline_total"] = 0

    return out


# ────────────────────────────────────────────────────────────────────
#  Smart Merge — Borderline review endpoints
# ────────────────────────────────────────────────────────────────────

@router.get("/borderline")
def list_borderline(project_id: str):
    """List Smart Merge's borderline queue (for manual approval)."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    from services import smart_merger
    return smart_merger.list_borderline(project_id)


class BorderlineDecision(BaseModel):
    pair_id: str
    decision: str  # "accept" | "reject" | "skip"


class BorderlineDecidePayload(BaseModel):
    decisions: list[BorderlineDecision] = Field(default_factory=list)


@router.post("/borderline/decide")
def decide_borderline(project_id: str, payload: BorderlineDecidePayload):
    """Apply the user's borderline decisions. Takes a snapshot, writes an audit record."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    with job_runner.mutation(project_id):
        from services import smart_merger
        decisions = [d.model_dump() for d in payload.decisions]
        if not decisions:
            raise HTTPException(400, "decisions_empty")
        valid = {"accept", "reject", "skip"}
        for d in decisions:
            if d["decision"] not in valid:
                raise HTTPException(400, f"invalid_decision: {d['decision']}")
        return smart_merger.decide_borderline(project_id, decisions)


# ────────────────────────────────────────────────────────────────────
#  Analysis management — every merge is an independent analysis folder
# ────────────────────────────────────────────────────────────────────

def _is_unfinished(meta: dict) -> bool:
    """An analysis a merge created (`source: user_run`) but never finalized.

    finalize_analysis stamps `completed_at`; a cancelled/killed merge leaves the folder
    without it. Legacy metas that have no `source` field are NOT treated as unfinished.
    """
    return meta.get("source") == "user_run" and not meta.get("completed_at")


@router.get("/analyses")
def list_analyses_endpoint(project_id: str):
    """List all analyses in this project, from newest to oldest.

    Her item: {id, label, method, created_at, completed_at?, file_count, total_size, is_active}
    """
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    return {
        "active_id": analyses.get_active_analysis_id(project_id),
        "items": [m for m in analyses.list_analyses(project_id)
                  if m.get("is_active") or not _is_unfinished(m)],
    }


@router.post("/analyses/{analysis_id}/activate")
def activate_analysis(project_id: str, analysis_id: str):
    """Make an analysis active — Records/Filter/Quality operate on it from then on."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    with job_runner.mutation(project_id):
        meta = analyses.get_analysis_meta(project_id, analysis_id)
        if not meta or _is_unfinished(meta):
            raise HTTPException(404, "analysis_not_found")
        analyses.set_active_analysis(project_id, analysis_id)
        # Invalidate the filter cache (the active dataset changed)
        try:
            from services import filter_engine
            filter_engine._DF_CACHE.clear()
        except Exception:
            pass
        # Audit
        try:
            from services import audit
            audit.write(
                project_id,
                kind="analysis_activate",
                title=f"Active analysis: {meta.get('label') or analysis_id}",
                title_key="audit.titles.analysisActivated",
                title_params={"label": meta.get("label") or analysis_id},
                details={
                    "analysis_id": analysis_id,
                    "method": meta.get("method"),
                    "label": meta.get("label"),
                },
                user_action="analysis_activate",
            )
        except Exception:
            pass
        return {"ok": True, "active_id": analysis_id}


@router.delete("/analyses/{analysis_id}")
def delete_analysis_endpoint(project_id: str, analysis_id: str):
    """Delete an analysis. On Windows, if there is a file lock it falls back
    to soft-delete (invisible in the UI but the files remain on disk). If it
    was active, the active status moves to the newest other analysis."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    with job_runner.mutation(project_id):
        meta = analyses.get_analysis_meta(project_id, analysis_id)
        if not meta:
            raise HTTPException(404, "analysis_not_found")
        ok = analyses.delete_analysis(project_id, analysis_id)
        if not ok:
            raise HTTPException(500, "analysis_delete_failed")
        # Clear the cache
        try:
            from services import filter_engine
            filter_engine._DF_CACHE.clear()
        except Exception:
            pass
        # Audit
        try:
            from services import audit
            audit.write(
                project_id,
                kind="analysis_delete",
                title=f"Analysis deleted: {meta.get('label') or analysis_id}",
                title_key="audit.titles.analysisDeleted",
                title_params={"label": meta.get("label") or analysis_id},
                details={
                    "analysis_id": analysis_id,
                    "method": meta.get("method"),
                },
                user_action="analysis_delete",
            )
        except Exception:
            pass
        active_id = analyses.get_active_analysis_id(project_id)
        # Delete this analysis's history records — every analysis has its OWN separate history
        # (Burak: "each one should have its own separate history"). Project-level setup
        # records (upload etc.) and the records of OTHER analyses are preserved.
        try:
            from services import audit as _audit
            _audit.delete_for_analysis(project_id, analysis_id)
        except Exception:
            pass
        return {"ok": True, "active_id": active_id}
