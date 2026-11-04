"""Append-only audit log recording every significant operation in a project.

Each event is appended as a line to a JSON Lines file (``operations.jsonl``)
with fields such as ts, kind, title, details, before/after, snapshot, and
user_action. Events are tagged with an analysis_id so each analysis keeps its
own history, while project-level events remain visible across all analyses.
Provides helpers to write and read entries, delete or clear logs, summarize
activity, and render a Markdown operation report.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

from services import doc_i18n, storage


_LOCK = threading.Lock()

# analysis_id sentinels — for a SEPARATE history per analysis.
_AUTO = "__auto__"  # write: automatically tag the entry with the active analysis
_ALL = "__all__"    # read: no filter (whole project)


def _log_path(project_id: str) -> Path:
    return storage.project_dir(project_id) / "operations.jsonl"


def write(
    project_id: str,
    kind: str,
    title: str,
    *,
    title_key: Optional[str] = None,
    title_params: Optional[dict[str, Any]] = None,
    details: Optional[dict[str, Any]] = None,
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
    snapshot: Optional[str] = None,
    user_action: Optional[str] = None,
    analysis_id: Optional[str] = _AUTO,
) -> dict[str, Any]:
    """Append a new entry. Errors are swallowed silently — a corrupted log must not stop the main flow.

    `title`  — human-readable text for backward compatibility / the markdown report (fallback).
    `title_key` + `title_params` — for UI display-time i18n (frontend t(key, params)).
    `analysis_id` — the analysis the entry belongs to (each analysis has its SEPARATE
        history). The default ("__auto__") finds the active analysis automatically;
        project-level operations (upload/file delete etc.) pass `analysis_id=None` →
        they appear as "setup" in all analyses.
    """
    if analysis_id == _AUTO:
        try:
            from services import analyses
            analysis_id = analyses.get_active_analysis_id(project_id)
        except Exception:
            analysis_id = None
    entry = {
        "ts": time.time(),
        "kind": kind,
        "title": title,
        "title_key": title_key,
        "title_params": title_params or None,
        "analysis_id": analysis_id,
        "details": details or {},
        "before": before,
        "after": after,
        "snapshot": snapshot,
        "user_action": user_action,
    }
    try:
        with _LOCK:
            p = _log_path(project_id)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass
    return entry


def read(
    project_id: str,
    limit: Optional[int] = None,
    analysis_id: Optional[str] = _ALL,
) -> list[dict[str, Any]]:
    """Read the audit entries.

    `analysis_id`:
        "__all__"   → no filter (the whole project history).
        <id> / None → that analysis's entries + project-level (analysis_id is None) setup
                      entries (upload etc.) — "what was done in that analysis".
    """
    p = _log_path(project_id)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    if analysis_id != _ALL:
        out = [e for e in out if e.get("analysis_id") == analysis_id or e.get("analysis_id") is None]
    if limit and limit > 0:
        out = out[-limit:]
    return out


def delete_for_analysis(project_id: str, analysis_id: str) -> int:
    """Delete an analysis's entries (called when that analysis is deleted). Project-level
    (analysis_id is None) setup entries and the entries of other analyses are PRESERVED."""
    p = _log_path(project_id)
    if not p.exists():
        return 0
    entries = read(project_id)  # all
    kept = [e for e in entries if e.get("analysis_id") != analysis_id]
    removed = len(entries) - len(kept)
    if removed <= 0:
        return 0
    try:
        with _LOCK:
            with p.open("w", encoding="utf-8") as f:
                for e in kept:
                    f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
    except Exception:
        return 0
    return removed


def clear(project_id: str) -> int:
    """Delete the whole log. An irreversible operation (with user confirmation)."""
    p = _log_path(project_id)
    if not p.exists():
        return 0
    count = sum(1 for _ in p.open("r", encoding="utf-8"))
    p.unlink()
    return count


def summary(project_id: str) -> dict[str, Any]:
    """Rough summary — per-category counts, first/last timestamp."""
    entries = read(project_id)
    if not entries:
        return {"total": 0, "by_kind": {}, "first_ts": None, "last_ts": None}
    by_kind: dict[str, int] = {}
    for e in entries:
        by_kind[e.get("kind", "?")] = by_kind.get(e.get("kind", "?"), 0) + 1
    return {
        "total": len(entries),
        "by_kind": by_kind,
        "first_ts": entries[0].get("ts"),
        "last_ts": entries[-1].get("ts"),
    }


# ----- Markdown report -----

# English category labels (kept for backward compatibility; documents use
# doc_i18n.kind_label(lang, kind) so they follow the requested language).
KIND_LABELS = {k[len("kind."):]: v for k, v in doc_i18n.STRINGS["en"].items() if k.startswith("kind.")}


def _as_int(value: Any) -> int:
    """Count from a stored detail value; a corrupt or missing value counts as 0."""
    try:
        return int(round(float(value or 0)))
    except Exception:
        return 0


def _render_smart_merge_md(details: dict, lang: str = "en") -> list[str]:
    """Rich Markdown block for a Smart Merge audit entry."""
    t = lambda key, **p: doc_i18n.tr_(lang, key, **p)  # noqa: E731
    n = lambda v: doc_i18n.fmt_int(v, lang)  # noqa: E731
    lines: list[str] = []
    wos = _as_int(details.get("wos_input"))
    scp = _as_int(details.get("scopus_input"))
    merged = _as_int(details.get("merged_count"))
    matched = _as_int(details.get("matched_pairs"))
    borderline = _as_int(details.get("borderline_count"))
    conflict = _as_int(details.get("conflict_count"))
    lost_wos = _as_int(details.get("lost_wos_count"))
    lost_scp = _as_int(details.get("lost_scopus_count"))
    duration = details.get("duration_seconds")

    # Flow summary
    lines.append(f"**{t('smart.summaryHeading')}**")
    lines.append("")
    lines.append(f"| {t('smart.metric')} | {t('smart.value')} |")
    lines.append("|---|---|")
    lines.append(f"| {t('smart.rawInput')} | {t('smart.rawInputValue', wos=n(wos), scopus=n(scp), total=n(wos + scp))} |")
    lines.append(f"| {t('smart.matchedPairs')} | **{n(matched)}** |")
    lines.append(f"| {t('smart.borderlinePending')} | {n(borderline)} |")
    lines.append(f"| {t('smart.lostWos')} | {n(lost_wos)} |")
    lines.append(f"| {t('smart.lostScopus')} | {n(lost_scp)} |")
    lines.append(f"| {t('smart.output')} | **{n(merged)}** |")
    dedup_rate = (wos + scp - merged) / (wos + scp) if (wos + scp) else 0
    lines.append(f"| {t('smart.dedupRate')} | {doc_i18n.fmt_percent(dedup_rate, lang)} |")
    if duration:
        lines.append(f"| {t('smart.duration')} | {doc_i18n.fmt_float(duration, lang, 1)} {t('smart.seconds')} |")
    lines.append("")

    # Match stages
    stages = details.get("match_stages") or {}
    if stages:
        lines.append(f"**{t('smart.stagesHeading')}** _(Hammerton 2013 + Caputo 2024)_:")
        lines.append("")
        lines.append(f"| {t('smart.stage')} | {t('smart.matches')} |")
        lines.append("|---|---|")
        for stage, count in sorted(stages.items(), key=lambda x: -x[1]):
            lines.append(f"| {doc_i18n.stage_label(lang, stage)} | {n(count)} |")
        lines.append("")

    # Conflict source distribution (Caputo 2024)
    field_dist = details.get("field_source_distribution") or {}
    if field_dist:
        total = sum(field_dist.values())
        lines.append(f"**{t('smart.conflictHeading')}** _({t('smart.conflictNote', conflict=n(conflict))})_:")
        lines.append("")
        lines.append(f"| {t('smart.preferredSource')} | {t('doc.count')} | {t('smart.percent')} |")
        lines.append("|---|---|---|")
        for src, count in sorted(field_dist.items(), key=lambda x: -x[1]):
            ratio = (count / total) if total else 0
            lines.append(f"| {doc_i18n.field_source_label(lang, src)} | {n(count)} | {doc_i18n.fmt_percent(ratio, lang)} |")
        lines.append("")

    # Generated files
    files = details.get("output_files") or []
    if files:
        lines.append(f"**{t('smart.filesHeading')}**")
        for f in files:
            lines.append(f"- `{f}`")
        lines.append("")

    return lines


def render_detail_value(lang: str, key: str, value: Any, *, max_len: int = 200) -> str:
    """One detail value as text: numbers per ``lang``, filter keys by UI names, long values clipped."""
    if key == "filter_keys" and isinstance(value, list):
        value = ", ".join(doc_i18n.filter_label(lang, str(x)) for x in value)
    elif key == "match_stages" and isinstance(value, dict):
        value = ", ".join(f"{doc_i18n.stage_label(lang, k)}: {doc_i18n.fmt_int(v, lang)}" for k, v in value.items())
    elif isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    else:
        value = doc_i18n.fmt_value(value, lang)
    text = str(value)
    if len(text) > max_len:
        text = text[:max_len] + "…"
    return text


def format_markdown_report(
    project_id: str,
    analysis_id: Optional[str] = _ALL,
    lang: str = "en",
) -> str:
    lang = doc_i18n.norm_lang(lang)
    t = lambda key, **p: doc_i18n.tr_(lang, key, **p)  # noqa: E731
    meta = storage.get_project(project_id)
    entries = read(project_id, analysis_id=analysis_id)
    name = meta.name if meta else project_id

    import datetime as dt
    lines: list[str] = []
    lines.append(f"# BibexPy v2 — {t('doc.title')}")
    lines.append("")
    lines.append(f"**{t('doc.project')}:** {name} (`{project_id}`)  ")
    lines.append(f"**{t('doc.generated')}:** {doc_i18n.fmt_datetime(dt.datetime.now(), lang)}  ")
    lines.append(f"**{t('doc.totalOps')}:** {doc_i18n.fmt_int(len(entries), lang)}")
    lines.append("")

    if not entries:
        lines.append(f"_{t('doc.noOps')}_")
        return "\n".join(lines)

    # Summary — from the filtered entries (active analysis history)
    by_kind: dict[str, int] = {}
    for e in entries:
        by_kind[e.get("kind", "?")] = by_kind.get(e.get("kind", "?"), 0) + 1
    lines.append(f"## {t('doc.summary')}")
    lines.append("")
    lines.append(f"| {t('doc.category')} | {t('doc.count')} |")
    lines.append("|---|---|")
    for k, n in sorted(by_kind.items(), key=lambda x: -x[1]):
        lines.append(f"| {doc_i18n.kind_label(lang, k)} | {doc_i18n.fmt_int(n, lang)} |")
    lines.append("")

    lines.append(f"## {t('doc.chronology')}")
    lines.append("")
    for i, e in enumerate(entries, 1):
        ts_str = doc_i18n.fmt_dt(e.get("ts"), lang, seconds=True)
        label = doc_i18n.kind_label(lang, e.get("kind", "?"))
        title = doc_i18n.resolve_title(lang, e)
        lines.append(f"### {i}. {label} — {title}")
        lines.append(f"_{ts_str}_")
        details = e.get("details")
        if not isinstance(details, dict):
            details = {}

        # Rich format for Smart Merge — not a JSON dump
        if e.get("kind") == "merge" and details.get("method") == "smart":
            lines.append("")
            lines.extend(_render_smart_merge_md(details, lang))
            if e.get("snapshot"):
                lines.append(f"- 📸 {t('doc.snapshot')}: `{e['snapshot']}`")
            lines.append("")
            continue

        if details:
            lines.append("")
            for k, v in details.items():
                if v is None or v == "":
                    continue
                lines.append(f"- **{doc_i18n.detail_label(lang, k)}:** {render_detail_value(lang, k, v)}")
        before = e.get("before")
        after = e.get("after")
        if before or after:
            lines.append("")
            if before:
                lines.append(f"- _{t('doc.before')}:_ `{json.dumps(before, ensure_ascii=False)}`")
            if after:
                lines.append(f"- _{t('doc.after')}:_ `{json.dumps(after, ensure_ascii=False)}`")
        if e.get("snapshot"):
            lines.append(f"- 📸 {t('doc.snapshot')}: `{e['snapshot']}`")
        if e.get("user_action"):
            lines.append(f"- 👤 {t('doc.user')}: _{doc_i18n.user_action_label(lang, e['user_action'])}_")
        lines.append("")
    return "\n".join(lines)
