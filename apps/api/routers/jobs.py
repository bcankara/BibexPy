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

    # If the job has already finished — send the final state immediately and close.
    # Race condition: the user subscribes as soon as they see it in the UI, and the
    # job may have completed in the meantime. In that case no event arrives after
    # subscribing and the stream looks "incomplete" (ERR_INCOMPLETE_CHUNKED_ENCODING).
    # Let us emit the current snapshot as the first event and then emit done.
    initial_snapshot = job.to_dict() if job else None
    finished_states = {"completed", "failed", "cancelled"}
    job_finished_already = (initial_snapshot or {}).get("status") in finished_states

    async def event_gen():
        try:
            # Initial snapshot — show the UI the current state right away
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

    # Use ping_message_factory to send a keep-alive comment line every 15 seconds
    # — this reduces cases where, on the reverse-proxy / browser side, the connection
    # looks half-cut (chunked encoding incomplete).
    return EventSourceResponse(event_gen(), ping=15)
