from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import settings
from jobs.runner import job_runner
from services import audit, storage
from services.disambiguation import pipeline

router = APIRouter(prefix="/projects/{project_id}/disambiguate", tags=["disambiguate"])


@router.get("/status")
def status(project_id: str):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    return {
        "enabled": settings.disambiguation_enabled,
        "configured": bool(settings.effective_llm_api_key),
        "model": settings.effective_llm_model,
        "provider": settings.llm_provider or "deepseek",
        "base_url": settings.effective_llm_base_url,
        "blocking_threshold": settings.disambiguation_blocking_threshold,
        "auto_approve_threshold": settings.disambiguation_auto_approve_threshold,
    }


class DisambiguateRequest(BaseModel):
    # `mode` geriye uyumluluk için durur; artık davranışı etkilemez (3-katman varsayılan).
    mode: str = "auto"
    # Opsiyonel maliyet/test sınırı — sadece ilk N kayıt taranır. None = tüm dataset.
    max_records: int | None = None


@router.post("/authors")
async def start_authors(project_id: str, payload: DisambiguateRequest | None = None):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    if not settings.disambiguation_enabled:
        raise HTTPException(400, "disambiguation_disabled_hint")
    max_records = payload.max_records if payload else None

    async def worker(ctx):
        return await pipeline.run_author_disambiguation(ctx, project_id, max_records=max_records)

    job = job_runner.submit(
        project_id, "disambiguate_authors", "Yazar Ayrıştırma",
        worker, title_key="audit.titles.authorDisambiguation", exclusive=True,
    )
    return {"job_id": job.id, "max_records": max_records}


@router.post("/affiliations")
async def start_affiliations(project_id: str, payload: DisambiguateRequest | None = None):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    if not settings.disambiguation_enabled:
        raise HTTPException(400, "disambiguation_disabled")
    max_records = payload.max_records if payload else None

    async def worker(ctx):
        return await pipeline.run_affiliation_disambiguation(ctx, project_id, max_records=max_records)

    job = job_runner.submit(
        project_id, "disambiguate_affiliations", "Kurum Normalizasyonu",
        worker, title_key="audit.titles.affiliationStandardization", exclusive=True,
    )
    return {"job_id": job.id}


@router.post("/countries")
async def start_countries(project_id: str, payload: DisambiguateRequest | None = None):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    if not settings.disambiguation_enabled:
        raise HTTPException(400, "disambiguation_disabled")
    max_records = payload.max_records if payload else None

    async def worker(ctx):
        return await pipeline.run_country_standardization(ctx, project_id, max_records=max_records)

    job = job_runner.submit(
        project_id, "disambiguate", "Ülke Standartlaştırma",
        worker, title_key="audit.titles.countryStandardization", exclusive=True,
    )
    return {"job_id": job.id}


@router.post("/organizations")
async def start_organizations(project_id: str, payload: DisambiguateRequest | None = None):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    if not settings.disambiguation_enabled:
        raise HTTPException(400, "disambiguation_disabled")
    max_records = payload.max_records if payload else None

    async def worker(ctx):
        return await pipeline.run_org_rollup(ctx, project_id, max_records=max_records)

    job = job_runner.submit(
        project_id, "disambiguate", "Kurum Toplama (üst kurum)",
        worker, title_key="audit.titles.orgRollup", exclusive=True,
    )
    return {"job_id": job.id}


@router.get("/proposals/{kind}")
def get_proposals(project_id: str, kind: str):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    if kind not in ("authors", "affiliations", "countries", "organizations"):
        raise HTTPException(400, "invalid_kind")
    return pipeline.list_proposals(project_id, kind)


class ApplyRequest(BaseModel):
    kind: str
    approved: list[dict[str, Any]] = []          # birleştirme (merge) onayları
    approved_splits: list[dict[str, Any]] = []   # ayrıştırma (split) onayları — yalnız authors


@router.post("/apply")
def apply(project_id: str, payload: ApplyRequest):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    if payload.kind not in ("authors", "affiliations", "countries", "organizations"):
        raise HTTPException(400, "invalid_kind")

    # Önce AYRIŞTIRMA (split), sonra BİRLEŞTİRME (merge) — sıra: ayrıştırma ilk işlem.
    split_result = None
    if payload.kind == "authors" and payload.approved_splits:
        split_result = pipeline.apply_splits(project_id, payload.approved_splits)
    merge_result = None
    if payload.approved:
        merge_result = pipeline.apply_clusters(project_id, payload.kind, payload.approved)

    replacements = (
        (split_result or {}).get("replacements", 0) + (merge_result or {}).get("replacements", 0)
    )
    snapshot = (merge_result or {}).get("snapshot") or (split_result or {}).get("snapshot")
    audit.write(
        project_id,
        kind="disambiguate",
        title=f"Yazar Ayrıştırma uygulandı ({payload.kind}) — {replacements} değişiklik",
        title_key="audit.titles.disambiguationApplied",
        title_params={"kind": payload.kind, "n": replacements},
        details={
            "kind": payload.kind,
            "clusters_approved": len(payload.approved),
            "splits_approved": len(payload.approved_splits),
            "replacements": replacements,
        },
        snapshot=snapshot,
        user_action="apply_disambiguation",
    )
    return {"merge": merge_result, "split": split_result, "replacements": replacements, "snapshot": snapshot}


class RestoreRequest(BaseModel):
    snapshot: str


@router.post("/restore")
def restore(project_id: str, payload: RestoreRequest):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    result = pipeline.restore_snapshot(project_id, payload.snapshot)
    audit.write(
        project_id,
        kind="snapshot_restore",
        title=f"Disambiguation snapshot geri yüklendi",
        title_key="audit.titles.disambiguationRestored",
        details={"snapshot": payload.snapshot, "into": result.get("into")},
        user_action="restore",
    )
    return result
