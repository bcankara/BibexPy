"""Builds a publication-ready data-flow summary from the audit log.

Derives the record-count pipeline for the ACTIVE analysis — identification
(raw inputs), deduplication (intra-source + cross-source matches with stage
breakdown), uncertain-pair decisions, and every subsequent exclusion step
(permanent filter applies, manual deletions) — ending at the current dataset
size. The Report page renders this as a downloadable flow diagram, so the
manuscript's data-preparation figure comes straight from the recorded
operations.
"""

from __future__ import annotations

from typing import Any, Optional

from services import analyses, audit


def build_flow(project_id: str) -> dict[str, Any]:
    """Aktif analizin kayıtlı operasyonlarından akış özetini çıkar."""
    aid: Optional[str] = None
    try:
        aid = analyses.get_active_analysis_id(project_id)
    except Exception:
        aid = None

    entries = audit.read(project_id, analysis_id=aid)

    # Son Smart Merge girdisi — akışın kökü
    merge_entry: Optional[dict] = None
    for e in entries:
        if e.get("kind") == "merge" and (e.get("details") or {}).get("method") == "smart":
            merge_entry = e  # sonuncusu kazanır (yeniden merge)

    flow: dict[str, Any] = {
        "analysis_id": aid,
        "has_merge": merge_entry is not None,
        "inputs": None,
        "intra_removed": 0,
        "matched_pairs": 0,
        "stages": {},
        "borderline_total": 0,
        "after_merge": None,
        "steps": [],
        "final_total": None,
    }

    merge_ts = 0.0
    if merge_entry is not None:
        d = merge_entry.get("details") or {}
        wos = int(d.get("wos_input") or 0)
        scp = int(d.get("scopus_input") or 0)
        flow["inputs"] = {"wos": wos, "scopus": scp, "total": wos + scp}
        flow["intra_removed"] = int(d.get("intra_wos_removed") or 0) + int(d.get("intra_scopus_removed") or 0)
        flow["matched_pairs"] = int(d.get("matched_pairs") or 0)
        flow["stages"] = d.get("match_stages") or {}
        flow["borderline_total"] = int(d.get("borderline_count") or 0)
        flow["after_merge"] = int(d.get("merged_count") or 0)
        merge_ts = float(merge_entry.get("ts") or 0)

    # Merge SONRASI dışlama adımları — kronolojik
    for e in entries:
        ts = float(e.get("ts") or 0)
        if ts <= merge_ts:
            continue
        kind = e.get("kind")
        d = e.get("details") or {}
        if kind == "filter_apply":
            flow["steps"].append({
                "kind": "filter_apply",
                "removed": int(d.get("removed") or 0),
                "after": int(d.get("after")) if d.get("after") is not None else None,
                "criteria": list(d.get("filter_keys") or []),
                "ts": ts,
            })
        elif kind == "records_delete":
            after = (e.get("after") or {}).get("total")
            flow["steps"].append({
                "kind": "records_delete",
                "removed": int(d.get("deleted") or 0),
                "after": int(after) if after is not None else None,
                "criteria": [],
                "ts": ts,
            })
        elif kind == "merge_borderline":
            applied = int(d.get("applied_changes") or 0)
            if applied > 0:
                flow["steps"].append({
                    "kind": "merge_borderline",
                    "removed": applied,
                    "after": None,
                    "criteria": [],
                    "ts": ts,
                })

    # Nihai kayıt sayısı — canlı dataset'ten (audit zinciri eksik olsa da doğru)
    try:
        from services import filter_engine
        flow["final_total"] = int(len(filter_engine.load_merged(project_id)))
    except Exception:
        flow["final_total"] = None

    return flow
