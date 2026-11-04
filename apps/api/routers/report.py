"""Report endpoints for the operation log and the methodology report.

Serves two output types in Markdown, plain text, and PDF formats:
  - Raw operation log: a faithful record of the processing pipeline.
  - Methodology report: an English narrative generated from the log via an LLM.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, Response

from services import analyses, audit, doc_i18n, methodology, report_export, report_flow, storage

router = APIRouter(prefix="/projects/{project_id}/report", tags=["report"])


def _require(project_id: str) -> None:
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")


def _pdf(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _attach_text(text: str, filename: str, media: str) -> Response:
    return Response(
        content=text,
        media_type=f"{media}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ───────────────────────── Raw log ─────────────────────────
#
# The log documents follow the UI language (`lang`: en|tr, default en) and are
# scoped like the History drawer and /audit/report.md: the active analysis's
# entries plus project-level setup entries.

def _log_scope(project_id: str) -> Optional[str]:
    return analyses.get_active_analysis_id(project_id)


@router.get("/log.md")
def log_markdown(project_id: str, lang: str = "en"):
    _require(project_id)
    lang = doc_i18n.norm_lang(lang)
    return _attach_text(
        audit.format_markdown_report(project_id, analysis_id=_log_scope(project_id), lang=lang),
        "operation_log.md", "text/markdown")


@router.get("/log.txt")
def log_text(project_id: str, lang: str = "en"):
    _require(project_id)
    lang = doc_i18n.norm_lang(lang)
    return _attach_text(
        report_export.format_text_report(project_id, lang=lang, analysis_id=_log_scope(project_id)),
        "operation_log.txt", "text/plain")


@router.get("/log.pdf")
def log_pdf(project_id: str, lang: str = "en"):
    _require(project_id)
    lang = doc_i18n.norm_lang(lang)
    meta = storage.get_project(project_id)
    title = doc_i18n.tr_(lang, "doc.pdfTitle", name=meta.name if meta else project_id)
    md = audit.format_markdown_report(project_id, analysis_id=_log_scope(project_id), lang=lang)
    return _pdf(report_export.render_pdf(title, md, lang=lang), "operation_log.pdf")


# ───────────────────────── Data flow diagram ─────────────────

@router.get("/flow")
def data_flow(project_id: str):
    """Record-count flow derived from the audit log — the Report page draws it
    as a downloadable flow diagram (SVG/PNG)."""
    _require(project_id)
    return report_flow.build_flow(project_id)


@router.get("/flow.pdf")
def data_flow_pdf(project_id: str, lang: str = "en"):
    """Vector (publication-ready) PDF version of the same diagram — lang: en|tr."""
    _require(project_id)
    if not report_flow.build_flow(project_id).get("has_merge"):
        raise HTTPException(409, "no_merge_for_flow")
    pdf = report_flow.render_flow_pdf(project_id, lang=lang)
    return _pdf(pdf, "bibexpy_data_flow.pdf")


# ───────────────────────── Methodology report (LLM) ─────────────────────────

@router.get("/methodology")
def methodology_get(project_id: str):
    _require(project_id)
    report = methodology.get_methodology(project_id)
    return report or {"text": None}


@router.post("/methodology")
def methodology_generate(project_id: str):
    _require(project_id)
    report = methodology.generate_methodology(project_id)
    audit.write(
        project_id,
        kind="report",
        title="Methodology report generated (LLM)",
        title_key="audit.titles.methodologyGenerated",
        details={"model": report.get("model"), "step_count": report.get("step_count"), "lang": "en"},
        user_action="generate_methodology",
    )
    return report


def _methodology_text_or_404(project_id: str) -> str:
    report = methodology.get_methodology(project_id)
    if not report or not report.get("text"):
        raise HTTPException(404, "methodology_not_generated")
    return report["text"]


@router.get("/methodology.md")
def methodology_md(project_id: str):
    _require(project_id)
    return _attach_text(_methodology_text_or_404(project_id), "methodology_report.md", "text/markdown")


@router.get("/methodology.txt")
def methodology_txt(project_id: str):
    _require(project_id)
    text = report_export._strip_emoji(_methodology_text_or_404(project_id))
    return _attach_text(text, "methodology_report.txt", "text/plain")


@router.get("/methodology.pdf")
def methodology_pdf(project_id: str):
    _require(project_id)
    text = _methodology_text_or_404(project_id)
    pdf = report_export.render_pdf(methodology.methodology_title(project_id), text)
    return _pdf(pdf, "methodology_report.pdf")
