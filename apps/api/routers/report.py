"""Rapor endpoint'leri — ham günlük (MD/TXT/PDF) + LLM metodoloji raporu (MD/TXT/PDF).

Export adımından sonraki son adım. İki çıktı türü:
  • Ham operasyon günlüğü — sürecin birebir kaydı.
  • Metodoloji raporu — günlüğün LLM ile İngilizce makale metnine dönüştürülmüş hali.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, Response

from services import audit, methodology, report_export, storage

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


# ───────────────────────── Ham günlük ─────────────────────────

@router.get("/log.md")
def log_markdown(project_id: str):
    _require(project_id)
    return _attach_text(audit.format_markdown_report(project_id), "operation_log.md", "text/markdown")


@router.get("/log.txt")
def log_text(project_id: str):
    _require(project_id)
    return _attach_text(report_export.format_text_report(project_id), "operation_log.txt", "text/plain")


@router.get("/log.pdf")
def log_pdf(project_id: str):
    _require(project_id)
    meta = storage.get_project(project_id)
    title = f"Operation Report — {meta.name if meta else project_id}"
    pdf = report_export.render_pdf(title, audit.format_markdown_report(project_id))
    return _pdf(pdf, "operation_log.pdf")


# ───────────────────────── Metodoloji raporu (LLM) ─────────────────────────

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
        title="Metodoloji raporu üretildi (LLM)",
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
