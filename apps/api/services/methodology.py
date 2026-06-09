"""LLM ile metodoloji raporu üretimi.

Operasyon günlüğü (audit log) bir LLM'e gönderilir; LLM, bibliometrik bir
makalenin "Veri Hazırlama / Metodoloji" alt bölümüne yazılabilecek İngilizce
bir anlatı döndürür. Sonuç projede saklanır ve MD/TXT/PDF olarak indirilebilir.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from config import settings
from services import report_export, storage
from services.disambiguation.deepseek_client import (
    DeepSeekClient, DeepSeekError, METHODOLOGY_SYSTEM_PROMPT,
)


# Üretilen metinde her zaman bulunması gereken BibexPy künyesi (APA7, kullanıcı zorunlu kıldı).
BIBEXPY_CITATION = (
    "Kara, B. C., Şahin, A., & Dirsehan, T. (2025). BibexPy: Harmonizing the "
    "bibliometric symphony of Scopus and Web of Science. SoftwareX, 30, 102098. "
    "https://doi.org/10.1016/j.softx.2025.102098"
)


def _ensure_citation(text: str) -> str:
    """LLM atfı eklemediyse deterministik olarak ekle (çift eklemeyi önle)."""
    low = text.lower()
    if "softx" in low or "dirsehan" in low or "102098" in low:
        return text
    return text.rstrip() + "\n\n## Reference\n\n" + BIBEXPY_CITATION


def _report_path(project_id: str) -> Path:
    return storage.project_dir(project_id) / "methodology_report.json"


def _client() -> DeepSeekClient:
    api_key = settings.effective_llm_api_key
    if not api_key:
        raise HTTPException(400, "llm_key_missing")
    return DeepSeekClient(
        api_key=api_key,
        base_url=settings.effective_llm_base_url,
        model=settings.effective_llm_model,
    )


def get_methodology(project_id: str) -> Optional[dict[str, Any]]:
    """Saklanan metodoloji raporunu döndür (yoksa None)."""
    p = _report_path(project_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def generate_methodology(project_id: str) -> dict[str, Any]:
    """Günlükten LLM ile İngilizce metodoloji raporu üret, sakla ve döndür."""
    digest = report_export.build_log_digest(project_id)
    if not digest.get("steps"):
        raise HTTPException(400, "no_operations")

    client = _client()  # anahtar yoksa burada llm_key_missing fırlatır
    try:
        text = client.chat_text(
            METHODOLOGY_SYSTEM_PROMPT,
            json.dumps(digest, ensure_ascii=False),
            temperature=0.3,
        )
    except DeepSeekError as e:
        raise HTTPException(502, f"llm_request_failed: {e}")

    report = {
        "text": _ensure_citation((text or "").strip()),
        "generated_at": time.time(),
        "model": settings.effective_llm_model,
        "provider": settings.llm_provider or "deepseek",
        "lang": "en",
        "step_count": len(digest["steps"]),
    }
    try:
        _report_path(project_id).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass
    return report


def methodology_title(project_id: str) -> str:
    meta = storage.get_project(project_id)
    name = meta.name if meta else project_id
    return f"Data Preparation Methodology — {name}"
