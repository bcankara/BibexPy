from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from jobs.runner import job_runner
from services import enricher, storage

router = APIRouter(prefix="/projects/{project_id}/enrich", tags=["enrich"])


class ApiEnrichRequest(BaseModel):
    sources: list[str] | None = None  # ileride seçimli kullanmak için


@router.post("/fill-all")
async def start_fill_all(project_id: str):
    """Birleşik doldurma: DOI başına tek API çağrısı, tüm boş alanlar (yalnız API; ML yoktur)."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")

    async def worker(ctx):
        return await enricher.run_fill_all(ctx, project_id)

    job = job_runner.submit(
        project_id, "enrich_api", "Eksik alanları doldur (API)", worker,
        title_key="audit.titles.fillAll", exclusive=True,
    )
    return {"job_id": job.id}


@router.post("/api")
async def start_api_enrichment(project_id: str, payload: ApiEnrichRequest):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")

    async def worker(ctx):
        return await enricher.run_api_enrichment(ctx, project_id, payload.sources)

    job = job_runner.submit(
        project_id, "enrich_api", "API ile zenginleştirme", worker,
        title_key="audit.titles.apiEnrichment", exclusive=True,
    )
    return {"job_id": job.id}
