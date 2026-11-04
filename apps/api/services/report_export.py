"""Report export helpers.

Produces two kinds of output, each available as Markdown, plain text, or PDF:
the raw operation audit log and the LLM-generated methodology report. PDFs are
rendered with reportlab; emoji and markdown symbols are stripped for the PDF
and plain-text variants while Markdown output stays fully formatted.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from typing import Any

from services import audit, storage

# ─────────────────────────────────────────────────────────────────────────
#  Text cleaning
# ─────────────────────────────────────────────────────────────────────────

# Emoji + dingbat + arrow ranges (reportlab's default font cannot render them)
_EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # emoji blocks
    "\U00002600-\U000027BF"   # misc symbols + dingbats
    "\U0001F1E6-\U0001F1FF"   # flags
    "\U00002190-\U000021FF"   # arrows (→ ↩ etc.)
    "\U00002B00-\U00002BFF"   # misc arrows/symbols
    "️"                  # variation selector
    "]+",
    flags=re.UNICODE,
)


def _strip_emoji(s: str) -> str:
    return _EMOJI.sub("", str(s)).strip()


def _esc_inline(s: str) -> str:
    """Safe escaping + limited markdown for reportlab Paragraph mini-HTML.

    XML special characters are escaped first, then **bold** and `code` markers
    are converted into controlled tags.
    """
    s = _strip_emoji(s)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`(.+?)`", r'<font face="Courier">\1</font>', s)
    return s


# ─────────────────────────────────────────────────────────────────────────
#  PDF generation (markdown-like text → PDF)
# ─────────────────────────────────────────────────────────────────────────

def render_pdf(title: str, md_text: str) -> bytes:
    """Convert a title + markdown-like body into a clean PDF.

    Supported: # / ## / ### headings, - / * bullets, | a | b | tables,
    **bold**, `code`, blank line = spacing. Emojis are dropped.
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
        title=_strip_emoji(title) or "Report", author="BibexPy v2",
    )
    base = getSampleStyleSheet()
    brand = colors.HexColor("#3F32B5")
    st_body = ParagraphStyle("body", parent=base["Normal"], fontSize=10, leading=14, alignment=TA_LEFT)
    st_h1 = ParagraphStyle("h1", parent=base["Heading1"], fontSize=17, spaceAfter=8, textColor=brand)
    st_h2 = ParagraphStyle("h2", parent=base["Heading2"], fontSize=13, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#1F2A44"))
    st_h3 = ParagraphStyle("h3", parent=base["Heading3"], fontSize=11, spaceBefore=8, spaceAfter=3)
    st_meta = ParagraphStyle("meta", parent=base["Normal"], fontSize=8.5, textColor=colors.HexColor("#64748B"))

    flow: list[Any] = []
    flow.append(Paragraph(_esc_inline(title), st_h1))
    flow.append(Paragraph(dt.datetime.now().strftime("%Y-%m-%d %H:%M"), st_meta))
    flow.append(Spacer(1, 10))

    lines = (md_text or "").split("\n")
    bullets: list[str] = []

    def flush_bullets() -> None:
        nonlocal bullets
        if bullets:
            flow.append(ListFlowable(
                [ListItem(Paragraph(b, st_body), leftIndent=12) for b in bullets],
                bulletType="bullet", start="•", leftIndent=10,
            ))
            flow.append(Spacer(1, 4))
            bullets = []

    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i].rstrip()
        stripped = raw.strip()

        # Table block
        if stripped.startswith("|"):
            flush_bullets()
            rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                i += 1
                # skip the separator row ( |---|---| )
                if cells and all(set(c) <= set("-: ") for c in cells):
                    continue
                rows.append(cells)
            if rows:
                ncol = max(len(r) for r in rows)
                data = [[Paragraph(_esc_inline(c), st_body) for c in (r + [""] * (ncol - len(r)))] for r in rows]
                tbl = Table(data, hAlign="LEFT")
                tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), brand),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]))
                flow.append(tbl)
                flow.append(Spacer(1, 6))
            continue

        if not stripped:
            flush_bullets()
            flow.append(Spacer(1, 5))
            i += 1
            continue
        if stripped.startswith("### "):
            flush_bullets(); flow.append(Paragraph(_esc_inline(stripped[4:]), st_h3)); i += 1; continue
        if stripped.startswith("## "):
            flush_bullets(); flow.append(Paragraph(_esc_inline(stripped[3:]), st_h2)); i += 1; continue
        if stripped.startswith("# "):
            flush_bullets(); flow.append(Paragraph(_esc_inline(stripped[2:]), st_h1)); i += 1; continue
        if stripped.startswith(("- ", "* ")):
            bullets.append(_esc_inline(stripped[2:])); i += 1; continue

        flush_bullets()
        flow.append(Paragraph(_esc_inline(stripped), st_body))
        i += 1

    flush_bullets()
    doc.build(flow)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────
#  Raw log → plain text
# ─────────────────────────────────────────────────────────────────────────

def format_text_report(project_id: str) -> str:
    """Clean plain-text version of the operation log, without markdown or emoji."""
    meta = storage.get_project(project_id)
    entries = audit.read(project_id)
    name = meta.name if meta else project_id

    out: list[str] = []
    out.append("BibexPy v2 - Operation Report")
    out.append("=" * 40)
    out.append(f"Project        : {name} ({project_id})")
    out.append(f"Generated      : {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    out.append(f"Total operations: {len(entries)}")
    out.append("")

    if not entries:
        out.append("No operations recorded yet.")
        return "\n".join(out)

    s = audit.summary(project_id)
    out.append("Summary")
    out.append("-" * 40)
    for k, c in sorted(s.get("by_kind", {}).items(), key=lambda x: -x[1]):
        label = _strip_emoji(audit.KIND_LABELS.get(k, k))
        out.append(f"  {label:<28} {c}")
    out.append("")

    out.append("Chronology")
    out.append("-" * 40)
    for idx, e in enumerate(entries, 1):
        ts = e.get("ts")
        ts_str = dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "-"
        label = _strip_emoji(audit.KIND_LABELS.get(e.get("kind", ""), e.get("kind", "?")))
        title = _strip_emoji(e.get("title", ""))
        out.append(f"{idx}. [{label}] {title}")
        out.append(f"   {ts_str}")
        details = e.get("details") or {}
        for k, v in details.items():
            if v is None or v == "":
                continue
            if isinstance(v, (list, dict)):
                v = str(v)
            v = str(v)
            if len(v) > 160:
                v = v[:160] + "..."
            out.append(f"   - {k}: {v}")
        if e.get("snapshot"):
            out.append(f"   - snapshot: {str(e['snapshot']).split('/')[-1].split(chr(92))[-1]}")
        out.append("")
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────
#  Log digest for the LLM methodology report
# ─────────────────────────────────────────────────────────────────────────

# Log kind → English action label (so the LLM writes in English)
_ACTION_EN = {
    "project_create": "Project created",
    "upload": "Source files uploaded",
    "convert": "Raw files converted to tabular format",
    "merge": "WoS/Scopus records merged and deduplicated",
    "merge_borderline": "Borderline duplicate pairs reviewed manually",
    "analysis_activate": "Active analysis selected",
    "analysis_delete": "Analysis removed",
    "filter_save": "Filter/screening preset defined",
    "records_delete": "Records removed (screening/exclusion)",
    "enrich_api": "Missing fields enriched via external APIs (e.g. Crossref/OpenAlex)",
    "enrich_ml": "Missing fields enriched via machine-learning prediction",
    "enrich_field": "Specific field enriched",
    "enrich_selected": "Selected records enriched",
    "enrich_selected_requested": "Selected records enriched",
    "disambiguate": "Author/affiliation names disambiguated (normalization/splitting)",
    "disambiguate_authors": "Author names disambiguated",
    "disambiguate_affiliations": "Affiliation names standardized",
    "snapshot_restore": "Dataset restored from a previous snapshot",
    "export": "Prepared dataset exported",
}


def _clean_metrics(details: dict) -> dict:
    """Noise-free subset of metrics for the digest — numbers + short values + small lists."""
    out: dict[str, Any] = {}
    for k, v in (details or {}).items():
        if v is None or v == "":
            continue
        if isinstance(v, bool) or isinstance(v, (int, float)):
            out[k] = v
        elif isinstance(v, str):
            if len(v) <= 120:
                out[k] = v
        elif isinstance(v, list):
            if len(v) <= 12 and all(isinstance(x, (str, int, float)) for x in v):
                short = [x for x in v if not (isinstance(x, str) and len(x) > 80)]
                if short:
                    out[k] = short
        elif isinstance(v, dict):
            if len(v) <= 12:
                out[k] = v
    return out


def build_log_digest(project_id: str) -> dict:
    """Clean chronological digest, with English actions, to be sent to the LLM."""
    meta = storage.get_project(project_id)
    entries = audit.read(project_id)
    steps: list[dict] = []
    for e in entries:
        kind = e.get("kind", "")
        # the job runner writes a completed/failed record for every job; keep only the meaningful ones
        ua = e.get("user_action") or ""
        if ua.startswith("job_") and ua != "job_completed":
            continue  # skip the intermediate failed/cancelled/queued/running records
        ts = e.get("ts")
        steps.append({
            "ts": dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else None,
            "kind": kind,
            "action": _ACTION_EN.get(kind, kind.replace("_", " ")),
            "metrics": _clean_metrics(e.get("details") or {}),
        })
    # Total record count (if available) — for context
    total_records = None
    try:
        from services import filter_engine
        df = filter_engine.load_merged(project_id)
        total_records = int(len(df))
    except Exception:
        total_records = None
    return {
        "project_name": meta.name if meta else project_id,
        "current_record_count": total_records,
        "steps": steps,
    }
