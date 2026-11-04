"""Data quality and chart API endpoints.

Exposes per-field fill-rate statistics and a weighted health score for a
project's merged dataset, an exportable overview table (CSV/XLSX), and
aggregated chart data (year, journal, document type, language, citation
distributions) consumed by the dashboard and records pages.
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
import pandas as pd

from config import settings
from services import analyses, audit, filter_engine, storage


router = APIRouter(prefix="/projects/{project_id}/quality", tags=["quality"])


# Fields tracked in the quality panel — aligned with the columns used by the
# VOSviewer & Biblioshiny analyses (criticality is in the hint text). A field is
# shown only if its column exists in the merged dataset (available=True).
# Label = i18n fallback; the UI takes the real label from recordDetail.fields.{code}.
QUALITY_FIELDS = [
    # ── Tier 1 — critical (co-authorship / citation networks + Biblioshiny core) ──
    ("AU", "Authors", "Critical: author & co-authorship analysis unit"),
    ("PY", "Year", "Critical: temporal analysis / production over time"),
    ("SO", "Source/Journal", "Critical: source coupling, Bradford, journal analysis"),
    ("TC", "Times Cited", "Critical: citation / impact analysis"),
    ("CR", "Cited References", "Critical: co-citation & bibliographic coupling"),
    # ── Tier 2 — important ──
    ("TI", "Title", "Important: term map, record identity"),
    ("DE", "Author Keywords", "Important: co-word / keyword co-occurrence"),
    ("ID", "Keywords Plus", "Important: co-word (WoS-generated)"),
    ("C1", "Addresses", "Important: country & institution collaboration"),
    # ── Tier 3 — useful ──
    ("AB", "Abstract", "Useful: term map, ML & disambiguation input"),
    ("AF", "Author Affiliations", "Useful: author-institution pairs"),
    ("WC", "WoS Categories", "Useful: thematic categorization"),
    ("SC", "Subject Categories", "Useful: higher-level categorization"),
    ("DI", "DOI", "Useful: citability, enrichment key"),
    ("LA", "Language", "Useful: language filtering"),
]

# Bibliometric WEIGHT — health_score weights fields by their analytical importance
# (the bar below shows "how much is empty"; health shows "how valuable the data is for
# bibliometric analysis"). The weights are derived from the VOSviewer/Biblioshiny
# criticality (Tier 1/2/3):
#   Tier 1 (critical)  = 3 : without them the main network analyses (co-authorship,
#                            co-citation, source, citation, time) cannot be built.
#   Tier 2 (important) = 2 : co-word, country/institution collaboration, title-term maps.
#   Tier 3 (useful)    = 1 : supplementary / filtering / identifier fields.
FIELD_WEIGHTS = {
    "AU": 3, "PY": 3, "SO": 3, "TC": 3, "CR": 3,        # Tier 1
    "TI": 2, "DE": 2, "ID": 2, "C1": 2,                 # Tier 2
    "AB": 1, "AF": 1, "WC": 1, "SC": 1, "DI": 1, "LA": 1,  # Tier 3
}


def _is_filled(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    return (s != "") & (s.str.upper() != "NAN") & s.notna()


def _compute_stats(df: pd.DataFrame) -> dict:
    """Per-field fill rate + weighted health score + DB distribution (shared by stats and overview)."""
    total = int(len(df))
    fields = []
    for code, label, hint in QUALITY_FIELDS:
        if code not in df.columns:
            fields.append({
                "field": code, "label": label, "hint": hint,
                "total": total, "filled": 0, "missing": total,
                "fill_rate": 0.0, "available": False,
            })
            continue
        filled_mask = _is_filled(df[code])
        filled = int(filled_mask.sum())
        fields.append({
            "field": code, "label": label, "hint": hint,
            "total": total, "filled": filled, "missing": total - filled,
            "fill_rate": (filled / total) if total else 0.0,
            "available": True,
        })

    num = sum(f["fill_rate"] * FIELD_WEIGHTS.get(f["field"], 1) for f in fields if f["available"])
    den = sum(FIELD_WEIGHTS.get(f["field"], 1) for f in fields if f["available"])
    health = float(num / den) if den else 0.0

    db_dist = {}
    if "DB" in df.columns:
        db_dist = df["DB"].astype(str).str.strip().value_counts().head(10).to_dict()
        db_dist = {str(k): int(v) for k, v in db_dist.items()}

    return {
        "total_records": total,
        "health_score": health,
        "fields": fields,
        "db_distribution": db_dist,
    }


@router.get("/last-fill-report")
def get_last_fill_report(project_id: str):
    """Return the summary of the most recent 'Fill all' (fill_all) operation — so the
    report stays on screen even after the page is refreshed. {"report": None} if it
    has never been run.

    In the audit log, a fill_all entry is stored as kind='enrich_api' + details.method=='fill_all'.
    """
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    # The report must belong ONLY to the current active analysis — a new merge (new
    # analysis) must not carry over the old enrichment report. Entries that differ
    # from the active analysis (or old entries without an analysis_id) are not shown.
    active_id = analyses.get_active_analysis_id(project_id)
    for entry in reversed(audit.read(project_id)):
        d = entry.get("details") or {}
        if entry.get("kind") == "enrich_api" and d.get("method") == "fill_all":
            if active_id is not None and d.get("analysis_id") != active_id:
                continue  # report of another (older) analysis — skip
            return {
                "report": {
                    "ts": entry.get("ts"),
                    "enriched": d.get("enriched", 0),
                    "api": d.get("api"),
                    "doi": d.get("doi"),
                    "fill_rate_before": d.get("fill_rate_before"),
                    "fill_rate_after": d.get("fill_rate_after"),
                    "per_field_fill": d.get("per_field_fill"),
                    "snapshot": d.get("snapshot") or entry.get("snapshot"),
                }
            }
    return {"report": None}


@router.get("/stats")
def get_quality_stats(project_id: str):
    """Fill rate %, missing count and top values for each field.

    health_score = bibliometric WEIGHTED average of fill rates (if critical fields are
    low the score drops sharply; it is barely affected if supplementary fields are
    missing). See _compute_stats.
    """
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    try:
        df = filter_engine.load_merged(project_id)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))
    return _compute_stats(df)


def _overview_cleanup(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


@router.get("/overview")
def download_overview(project_id: str, background_tasks: BackgroundTasks, fmt: str = "csv"):
    """Download the Overview table (Biblioshiny-style summary) — CSV or XLSX.

    A table that can be pasted into a report: field code · label · total · filled · missing · fill rate %.
    The XLSX also contains a 'Summary' sheet (total records, health score, DB distribution).
    PNG output is generated on the client side (html-to-image); the backend only provides the table.
    """
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    if fmt not in ("csv", "xlsx"):
        raise HTTPException(400, "invalid_format")
    try:
        df = filter_engine.load_merged(project_id)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))

    stats = _compute_stats(df)
    fields_table = pd.DataFrame([
        {
            "Field": f["field"],
            "Label": f["label"],
            "Total": f["total"],
            "Filled": f["filled"],
            "Missing": f["missing"],
            "Fill rate (%)": round(f["fill_rate"] * 100, 1),
        }
        for f in stats["fields"] if f["available"]
    ])

    stamp = time.strftime("%Y%m%d_%H%M%S")
    workdir = settings.storage_path / "tools_tmp" / uuid.uuid4().hex[:12]
    workdir.mkdir(parents=True, exist_ok=True)
    background_tasks.add_task(_overview_cleanup, workdir)
    out_path = workdir / f"data_health_{stamp}.{fmt}"

    if fmt == "csv":
        # utf-8-sig → so that Excel opens Turkish characters correctly.
        fields_table.to_csv(out_path, index=False, encoding="utf-8-sig")
    else:
        summary_rows = [
            {"Metric": "Total records", "Value": stats["total_records"]},
            {"Metric": "Health score (%)", "Value": round(stats["health_score"] * 100, 1)},
        ]
        for db, n in (stats.get("db_distribution") or {}).items():
            summary_rows.append({"Metric": f"Database · {db}", "Value": n})
        with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
            pd.DataFrame(summary_rows).to_excel(xw, sheet_name="Summary", index=False)
            fields_table.to_excel(xw, sheet_name="Fields", index=False)

    media = "text/csv" if fmt == "csv" else \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return FileResponse(path=str(out_path), filename=out_path.name, media_type=media)


@router.get("/charts")
def get_charts(project_id: str):
    """Chart data for the visualizations."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    try:
        df = filter_engine.load_merged(project_id)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))

    out: dict = {}

    # Year histogram + breakdown by doc_type
    if "PY" in df.columns:
        years = pd.to_numeric(df["PY"], errors="coerce").dropna().astype(int)
        if len(years):
            year_counts = years.value_counts().sort_index()
            out["year_histogram"] = [{"year": int(y), "count": int(c)} for y, c in year_counts.items()]

            # Year × doc type cross-tab (for the stacked bar)
            if "DT" in df.columns:
                df2 = df.copy()
                df2["_y"] = pd.to_numeric(df2["PY"], errors="coerce")
                df2 = df2.dropna(subset=["_y"])
                df2["_y"] = df2["_y"].astype(int)
                # Take the 5 most frequent types
                top_types = df2["DT"].astype(str).str.upper().value_counts().head(5).index.tolist()
                ct = pd.crosstab(df2["_y"], df2["DT"].astype(str).str.upper())
                ct = ct[[c for c in top_types if c in ct.columns]]
                out["year_by_doctype"] = {
                    "types": top_types,
                    "data": [
                        {"year": int(y), **{t: int(ct.loc[y].get(t, 0)) for t in top_types}}
                        for y in ct.index
                    ],
                }

    # Top 15 journals (SO)
    if "SO" in df.columns:
        so_counts = df["SO"].astype(str).str.strip().replace("", pd.NA).dropna().value_counts().head(15)
        out["top_journals"] = [{"name": str(k), "count": int(v)} for k, v in so_counts.items()]

    # Doc type distribution (pie)
    if "DT" in df.columns:
        dt_counts = df["DT"].astype(str).str.strip().str.upper().replace("", pd.NA).dropna().value_counts().head(10)
        out["doc_types"] = [{"name": str(k), "count": int(v)} for k, v in dt_counts.items()]

    # Language distribution
    if "LA" in df.columns:
        la_counts = df["LA"].astype(str).str.strip().str.upper().replace("", pd.NA).dropna().value_counts().head(10)
        out["languages"] = [{"name": str(k), "count": int(v)} for k, v in la_counts.items()]

    # Citation distribution (histogram buckets)
    if "TC" in df.columns:
        tc = pd.to_numeric(df["TC"], errors="coerce").dropna().astype(int)
        if len(tc):
            # 0, 1-5, 6-10, 11-25, 26-50, 51-100, 100+
            buckets = [(0, 0), (1, 5), (6, 10), (11, 25), (26, 50), (51, 100), (101, 10**9)]
            labels = ["0", "1-5", "6-10", "11-25", "26-50", "51-100", "100+"]
            data = []
            for (lo, hi), lab in zip(buckets, labels):
                if lo == 0 and hi == 0:
                    n = int((tc == 0).sum())
                else:
                    n = int(((tc >= lo) & (tc <= hi)).sum())
                data.append({"bucket": lab, "count": n})
            out["citation_buckets"] = data

    return out
