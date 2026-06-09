import asyncio
import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from jobs.runner import job_runner

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(project_id: Optional[str] = None):
    return [j.to_dict() for j in job_runner.list(project_id)]


@router.get("/{job_id}")
def get_job(job_id: str):
    job = job_runner.get(job_id)
    if not job:
        raise HTTPException(404, "job_not_found")
    return job.to_dict()


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str):
    if not job_runner.cancel(job_id):
        raise HTTPException(404, "job_not_found_or_uncancelable")
    return {"ok": True}


@router.get("/{job_id}/stream")
async def stream_job(job_id: str):
    job = job_runner.get(job_id)
    if not job:
        raise HTTPException(404, "job_not_found")

    queue = job_runner.subscribe(job_id)

    # Eğer job zaten bitmiş ise — son durumu hemen gönder ve kapat.
    # Race condition: kullanıcı UI'da görür görmez subscribe oluyor, bu sırada job
    # tamamlanmış olabilir. Bu durumda subscribe-ettikten sonra hiçbir event gelmez
    # ve stream "yarım" görünür (ERR_INCOMPLETE_CHUNKED_ENCODING). Mevcut snapshot'u
    # ilk event olarak emit edip ardından done emit edelim.
    initial_snapshot = job.to_dict() if job else None
    finished_states = {"completed", "failed", "cancelled"}
    job_finished_already = (initial_snapshot or {}).get("status") in finished_states

    async def event_gen():
        try:
            # İlk snapshot — UI'a anında güncel durumu göster
            if initial_snapshot is not None:
                yield {"event": "update", "data": json.dumps(initial_snapshot)}
                if job_finished_already:
                    yield {"event": "done", "data": "1"}
                    return
            while True:
                msg = await queue.get()
                if msg is None:
                    yield {"event": "done", "data": "1"}
                    return
                yield {"event": "update", "data": json.dumps(msg)}
        except asyncio.CancelledError:
            return
        finally:
            job_runner.unsubscribe(job_id, queue)

    # ping_message_factory ile her 15 saniyede bir keep-alive yorum satırı gönder
    # — bu, bazı reverse-proxy / browser tarafında bağlantıyı yarım kesik (chunked
    # encoding incomplete) gibi gösteren durumları azaltır.
    return EventSourceResponse(event_gen(), ping=15)
