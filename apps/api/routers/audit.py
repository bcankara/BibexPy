"""API endpoints for project audit logs.

Provides routes to list, summarize, append, and clear audit entries for a
project, plus a Markdown report export. Entries are scoped to the project's
currently active analysis.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from typing import Any, Optional

from services import analyses, audit, storage


router = APIRouter(prefix="/projects/{project_id}/audit", tags=["audit"])


class AuditEntry(BaseModel):
    kind: str
    title: str
    details: Optional[dict[str, Any]] = None
    user_action: Optional[str] = None


@router.get("")
def list_entries(project_id: str, limit: int = 200):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    # Aktif analizin geçmişi (+ proje-seviye kurulum) — her analizin AYRI geçmişi.
    active = analyses.get_active_analysis_id(project_id)
    return audit.read(project_id, limit=limit, analysis_id=active)


@router.get("/summary")
def get_summary(project_id: str):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    return audit.summary(project_id)


@router.post("")
def add_entry(project_id: str, payload: AuditEntry):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    return audit.write(
        project_id,
        kind=payload.kind,
        title=payload.title,
        details=payload.details,
        user_action=payload.user_action,
    )


@router.delete("", status_code=204)
def clear_entries(project_id: str):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    audit.clear(project_id)
    return None


@router.get("/report.md", response_class=PlainTextResponse)
def markdown_report(project_id: str):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    active = analyses.get_active_analysis_id(project_id)
    return audit.format_markdown_report(project_id, analysis_id=active)
