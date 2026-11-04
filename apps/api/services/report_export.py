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
from typing import Any, Optional

from services import audit, doc_i18n, report_flow, storage

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


def _esc_inline(s: str, *, code_font: str = "Courier", ascii_fold: bool = False) -> str:
    """Safe escaping + limited markdown for reportlab Paragraph mini-HTML.

    XML special characters are escaped first, then **bold** and `code` markers
    are converted into controlled tags. `ascii_fold` converts Turkish letters to
    ASCII (used only when no Unicode TTF is available).
    """
    s = _strip_emoji(s)
    if ascii_fold:
        s = s.translate(report_flow._ASCII_MAP)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`(.+?)`", lambda m: f'<font face="{code_font}">{m.group(1)}</font>', s)
    return s


# ─────────────────────────────────────────────────────────────────────────
#  PDF generation (markdown-like text → PDF)
# ─────────────────────────────────────────────────────────────────────────

def render_pdf(title: str, md_text: str, lang: str = "en") -> bytes:
    """Convert a title + markdown-like body into a clean PDF.

    Supported: # / ## / ### headings, - / * bullets, | a | b | tables,
    **bold**, `code`, blank line = spacing. Emojis are dropped. A Unicode TTF is
    registered (same discovery as the flow PDF) so Turkish letters render; only
    when none exists are they folded to ASCII. `lang` localises the date stamp.
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    from reportlab.pdfbase import pdfmetrics

    lang = doc_i18n.norm_lang(lang)
    font_reg, font_bold, unicode_ok = report_flow._fonts()
    if unicode_ok:
        pdfmetrics.registerFontFamily(
            font_reg, normal=font_reg, bold=font_bold, italic=font_reg, boldItalic=font_bold)

    def esc(text: str) -> str:
        return _esc_inline(text, code_font=font_reg if unicode_ok else "Courier",
                           ascii_fold=not unicode_ok)

    doc_title = _strip_emoji(title) or doc_i18n.tr_(lang, "doc.fallbackTitle")
    if not unicode_ok:
        doc_title = doc_title.translate(report_flow._ASCII_MAP)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
        title=doc_title, author="BibexPy v2",
    )
    base = getSampleStyleSheet()
    brand = colors.HexColor("#3F32B5")
    st_body = ParagraphStyle("body", parent=base["Normal"], fontName=font_reg, fontSize=10, leading=14, alignment=TA_LEFT)
    st_head = ParagraphStyle("thead", parent=st_body, fontName=font_bold, textColor=colors.white)
    st_h1 = ParagraphStyle("h1", parent=base["Heading1"], fontName=font_bold, fontSize=17, spaceAfter=8, textColor=brand)
    st_h2 = ParagraphStyle("h2", parent=base["Heading2"], fontName=font_bold, fontSize=13, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#1F2A44"))
    st_h3 = ParagraphStyle("h3", parent=base["Heading3"], fontName=font_bold, fontSize=11, spaceBefore=8, spaceAfter=3)
    st_meta = ParagraphStyle("meta", parent=base["Normal"], fontName=font_reg, fontSize=8.5, textColor=colors.HexColor("#64748B"))

    flow: list[Any] = []
    flow.append(Paragraph(esc(title), st_h1))
    flow.append(Paragraph(doc_i18n.fmt_datetime(dt.datetime.now(), lang), st_meta))
    flow.append(Spacer(1, 10))

    lines = (md_text or "").split("\n")
    bullets: list[str] = []

    def flush_bullets() -> None:
        nonlocal bullets
        if bullets:
            flow.append(ListFlowable(
                [ListItem(Paragraph(b, st_body), leftIndent=12) for b in bullets],
                bulletType="bullet", start="•", leftIndent=10, bulletFontName=font_reg,
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
                data = [
                    [Paragraph(esc(c), st_head if ri == 0 else st_body) for c in (r + [""] * (ncol - len(r)))]
                    for ri, r in enumerate(rows)
                ]
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
            flush_bullets(); flow.append(Paragraph(esc(stripped[4:]), st_h3)); i += 1; continue
        if stripped.startswith("## "):
            flush_bullets(); flow.append(Paragraph(esc(stripped[3:]), st_h2)); i += 1; continue
        if stripped.startswith("# "):
            flush_bullets(); flow.append(Paragraph(esc(stripped[2:]), st_h1)); i += 1; continue
        if stripped.startswith(("- ", "* ")):
            bullets.append(esc(stripped[2:])); i += 1; continue

        flush_bullets()
        flow.append(Paragraph(esc(stripped), st_body))
        i += 1

    flush_bullets()
    doc.build(flow)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────
#  Raw log → plain text
# ─────────────────────────────────────────────────────────────────────────

def format_text_report(
    project_id: str,
    lang: str = "en",
    analysis_id: Optional[str] = audit._ALL,
) -> str:
    """Clean plain-text version of the operation log, without markdown or emoji.

    `analysis_id` scopes the entries exactly like `audit.read` (default: the whole project).
    """
    lang = doc_i18n.norm_lang(lang)
    t = lambda key, **p: doc_i18n.tr_(lang, key, **p)  # noqa: E731
    meta = storage.get_project(project_id)
    entries = audit.read(project_id, analysis_id=analysis_id)
    name = meta.name if meta else project_id

    out: list[str] = []
    out.append(f"BibexPy v2 - {t('doc.title')}")
    out.append("=" * 40)
    out.append(f"{t('doc.project') + ':':<20}{name} ({project_id})")
    out.append(f"{t('doc.generated') + ':':<20}{doc_i18n.fmt_datetime(dt.datetime.now(), lang)}")
    out.append(f"{t('doc.totalOps') + ':':<20}{doc_i18n.fmt_int(len(entries), lang)}")
    out.append("")

    if not entries:
        out.append(t("doc.noOps"))
        return "\n".join(out)

    by_kind: dict[str, int] = {}
    for e in entries:
        by_kind[e.get("kind", "?")] = by_kind.get(e.get("kind", "?"), 0) + 1
    out.append(t("doc.summary"))
    out.append("-" * 40)
    for k, c in sorted(by_kind.items(), key=lambda x: -x[1]):
        label = _strip_emoji(doc_i18n.kind_label(lang, k))
        out.append(f"  {label:<28} {doc_i18n.fmt_int(c, lang)}")
    out.append("")

    out.append(t("doc.chronology"))
    out.append("-" * 40)
    for idx, e in enumerate(entries, 1):
        ts_str = doc_i18n.fmt_dt(e.get("ts"), lang, seconds=True)
        label = _strip_emoji(doc_i18n.kind_label(lang, e.get("kind", "?")))
        title = _strip_emoji(doc_i18n.resolve_title(lang, e))
        out.append(f"{idx}. [{label}] {title}")
        out.append(f"   {ts_str}")
        details = e.get("details")
        if not isinstance(details, dict):
            details = {}
        for k, v in details.items():
            if v is None or v == "":
                continue
            text = audit.render_detail_value(lang, k, v, max_len=160)
            out.append(f"   - {doc_i18n.detail_label(lang, k)}: {text}")
        if e.get("snapshot"):
            out.append(f"   - {t('doc.snapshot')}: {str(e['snapshot']).split('/')[-1].split(chr(92))[-1]}")
        if e.get("user_action"):
            out.append(f"   - {t('doc.user')}: {doc_i18n.user_action_label(lang, e['user_action'])}")
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


# Identifier-like token: ASCII letters/digits and a few separators, no free text
_TOKEN = re.compile(r"^[A-Za-z0-9_.+/:\- ]{1,40}$")


def _clean_value(v: Any, depth: int = 0) -> Any:
    """Digest-safe form of one metric value, or ``None`` when nothing safe remains.

    Numbers and bools pass; strings pass only as short identifier-like tokens (free
    text, e.g. localised or user-typed values, never reaches the LLM); small lists
    and dicts are cleaned recursively.
    """
    if isinstance(v, bool) or isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        return v if _TOKEN.match(v) else None
    if isinstance(v, list):
        if len(v) <= 12 and all(isinstance(x, (str, int, float)) for x in v):
            short = [x for x in v if not isinstance(x, str) or _TOKEN.match(x)]
            return short or None
        return None
    if isinstance(v, dict):
        if len(v) > 12 or depth >= 2:
            return None
        cleaned = {}
        for k, x in v.items():
            cx = _clean_value(x, depth + 1)
            if cx is not None and cx != "" and _TOKEN.match(str(k)):
                cleaned[str(k)] = cx
        return cleaned or None
    return None


def _clean_metrics(details: dict) -> dict:
    """Noise-free subset of metrics for the digest — numbers + short tokens + small lists/dicts."""
    out: dict[str, Any] = {}
    for k, v in (details or {}).items():
        if v is None or v == "":
            continue
        cv = _clean_value(v)
        if cv is not None:
            out[k] = cv
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
