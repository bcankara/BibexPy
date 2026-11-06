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
    # `mode` is kept for backward compatibility; it no longer affects behavior (3-tier is the default).
    mode: str = "auto"
    # Optional cost/test limit — only the first N records are scanned. None = the whole dataset.
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
        project_id, "disambiguate_authors", "Author disambiguation",
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
        project_id, "disambiguate_affiliations", "Affiliation standardization",
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
        project_id, "disambiguate", "Country standardization",
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
        project_id, "disambiguate", "Affiliation roll-up (parent organization)",
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
    approved: list[dict[str, Any]] = []          # merge approvals
    # Split approvals — authors only. Only each entry's `split_id` is read: the groups and
    # their row UIDs come from the stored proposal file, anything else in the entry is ignored.
    approved_splits: list[dict[str, Any]] = []


@router.post("/apply")
def apply(project_id: str, payload: ApplyRequest):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    if payload.kind not in ("authors", "affiliations", "countries", "organizations"):
        raise HTTPException(400, "invalid_kind")
    # Applying rewrites the dataset: never while an exclusive job could overwrite it.
    with job_runner.mutation(project_id):

        # SPLIT first, then MERGE — order: the split is the first operation.
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
            title=f"Disambiguation applied ({payload.kind}) — {replacements} changes",
            title_key="audit.titles.disambiguationApplied",
            title_params={"kind": payload.kind, "n": replacements},
            details={
                "kind": payload.kind,
                "clusters_approved": len(payload.approved),
                "splits_approved": len(payload.approved_splits),
                "replacements": replacements,
                # Split apply is UID-based: rows whose UID vanished since the scan are skipped
                # and reported, never mapped to another row.
                "applied_rows": (split_result or {}).get("applied_rows", 0),
                "skipped_missing": (split_result or {}).get("skipped_missing", 0),
                "changed_uids": (split_result or {}).get("changed_uids", []),
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
    with job_runner.mutation(project_id):
        result = pipeline.restore_snapshot(project_id, payload.snapshot)
        audit.write(
            project_id,
            kind="snapshot_restore",
            title="Disambiguation snapshot restored",
            title_key="audit.titles.disambiguationRestored",
            details={"snapshot": payload.snapshot, "into": result.get("into")},
            user_action="restore",
        )
        return result
