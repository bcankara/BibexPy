"""Simple, single-user job runner.

Runs background jobs as asyncio tasks with in-memory state, log, and progress
tracking, streamed live to clients over SSE. Provides per-project exclusivity to
prevent overlapping heavy jobs and a dedicated thread pool for cancellable
CPU-bound work so the rest of the app stays responsive.
"""

from __future__ import annotations

import asyncio
import functools
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional


# A DEDICATED, bounded pool for heavy CPU work (the disambiguation blocking builders).
# Why: if these jobs run via `asyncio.to_thread`, they use the SHARED default thread pool.
# When a scan is cancelled, `task.cancel()` only cancels the asyncio task — the thread
# running a function such as `build_author_blocks` CANNOT be KILLED in Python. The orphan
# threads of cancelled scans exhaust the default pool → ALL `to_thread` jobs, including
# export/merge/conversion and SPA serving, get queued = the application locks up
# (test finding #7). Once the heavy work is moved to a separate pool, the orphans stay
# isolated here; the default pool (the rest of the application) keeps responding. Thanks
# to the cooperative cancellation (should_cancel) in blocking.py, the orphans finish
# within roughly one iteration anyway.
_CPU_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bibex-cpu")


async def run_cpu(fn: Callable, *args, **kwargs):
    """Run heavy/cancellable CPU work in the DEDICATED pool (protect the default pool).

    Use this instead of `asyncio.to_thread`: orphan threads stay in the isolated pool and
    do not lock up the rest of the application (export/merge/serving)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_CPU_EXECUTOR, functools.partial(fn, *args, **kwargs))


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


@dataclass
class Job:
    id: str
    project_id: str
    kind: str
    title: str
    # i18n: title_key + title_params → the frontend (JobProgress) translates at display time.
    # The title field stays as a readable fallback (markdown/legacy).
    title_key: Optional[str] = None
    title_params: Optional[dict[str, Any]] = None
    # exclusive: if another exclusive job is already active in the same project, it is
    # REJECTED (prevents heavy scan/fill jobs from overlapping and exhausting the thread
    # pool — #7).
    exclusive: bool = False
    status: JobStatus = JobStatus.queued
    progress: float = 0.0
    log: list[str] = field(default_factory=list)
    result: Any = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "kind": self.kind,
            "title": self.title,
            "title_key": self.title_key,
            "title_params": self.title_params or None,
            "status": self.status.value,
            "progress": round(self.progress, 3),
            "log_tail": self.log[-50:],
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "created_at": self.created_at,
        }


class JobContext:
    """Control object passed to the worker function."""

    def __init__(self, job: Job, runner: "JobRunner"):
        self._job = job
        self._runner = runner

    def log(self, line: str) -> None:
        self._job.log.append(line)
        self._runner._notify(self._job.id)

    def progress(self, value: float) -> None:
        self._job.progress = max(0.0, min(1.0, value))
        self._runner._notify(self._job.id)

    @property
    def cancelled(self) -> bool:
        return self._job.status == JobStatus.cancelled


JobWorker = Callable[[JobContext], Awaitable[Any]]


class JobRunner:
    """In-memory job registry + asyncio tasks."""

    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._listeners: dict[str, set[asyncio.Queue]] = {}

    # --- CRUD ---
    def list(self, project_id: Optional[str] = None) -> list[Job]:
        items = list(self._jobs.values())
        if project_id:
            items = [j for j in items if j.project_id == project_id]
        items.sort(key=lambda j: j.created_at, reverse=True)
        return items

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status not in (JobStatus.queued, JobStatus.running):
            return False
        job.status = JobStatus.cancelled
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        self._notify(job_id, done=True)
        return True

    def has_active_exclusive(self, project_id: str) -> bool:
        """Is there an active (queued/running) exclusive job in this project?"""
        return any(
            j.project_id == project_id and j.exclusive
            and j.status in (JobStatus.queued, JobStatus.running)
            for j in self._jobs.values()
        )

    # --- enqueue ---
    def submit(
        self,
        project_id: str,
        kind: str,
        title: str,
        worker: JobWorker,
        *,
        title_key: Optional[str] = None,
        title_params: Optional[dict[str, Any]] = None,
        exclusive: bool = False,
    ) -> Job:
        # Single-active-job guard (#7): when an exclusive job is requested and the same
        # project already has an exclusive job, reject it. Even if the frontend button lock
        # gets out of sync (the trigger of the old freeze bug), heavy jobs do not overlap.
        if exclusive and self.has_active_exclusive(project_id):
            from fastapi import HTTPException
            raise HTTPException(409, "job_already_running")
        job = Job(
            id=uuid.uuid4().hex[:12], project_id=project_id, kind=kind,
            title=title, title_key=title_key, title_params=title_params,
            exclusive=exclusive,
        )
        self._jobs[job.id] = job
        task = asyncio.create_task(self._run(job, worker))
        self._tasks[job.id] = task
        return job

    async def _run(self, job: Job, worker: JobWorker) -> None:
        ctx = JobContext(job, self)
        try:
            job.status = JobStatus.running
            job.started_at = time.time()
            self._notify(job.id)
            result = await worker(ctx)
            if job.status == JobStatus.cancelled:
                return
            job.result = result
            job.progress = 1.0
            job.status = JobStatus.completed
        except asyncio.CancelledError:
            job.status = JobStatus.cancelled
            raise
        except Exception as e:
            job.status = JobStatus.failed
            job.error = str(e)
            job.log.append("HATA: " + str(e))
            job.log.append(traceback.format_exc())
        finally:
            job.finished_at = time.time()
            self._notify(job.id, done=True)
            # Automatic audit log write — at the end of every job
            self._write_audit(job)

    def _write_audit(self, job: Job) -> None:
        """Write to the audit log after the job completes."""
        try:
            from services import audit  # avoid the circular import
            duration = (job.finished_at - job.started_at) if (job.started_at and job.finished_at) else None
            details: dict = {
                "job_id": job.id,
                "duration_seconds": round(duration, 2) if duration else None,
                "status": job.status.value,
            }
            if isinstance(job.result, dict):
                # Also include the relevant parts of the result fields (without making it verbose)
                for k in ("method", "scopus_input", "wos_input", "merged_count", "duplicates_removed",
                          "stats", "enriched_count", "updated_count", "replacements", "snapshot",
                          "candidates", "clusters", "uncertain", "output_dataset",
                          "clusters_proposed", "splits_proposed", "auto",
                          "orcid_rows_filled", "dois_fetched",  # ORCID disambiguation write-back
                          # Field/selected enrichment
                          "field", "label", "total", "missing_before", "no_doi", "target",
                          "enriched", "api_no_data", "failed", "missing_after",
                          "fill_rate_before", "fill_rate_after", "per_field_fill",
                          "api", "doi",  # fill_all sub-summaries (for the enrichment report)
                          "api_success_by_source", "fields_updated", "doi_missing",
                          # Smart Merge fields
                          "matched_pairs", "borderline_count", "borderline_pending",
                          "conflict_count", "match_stages", "field_source_distribution",
                          "lost_wos_count", "lost_scopus_count", "output_files",
                          # Analysis folder
                          "analysis_id"):
                    if k in job.result:
                        v = job.result[k]
                        if isinstance(v, list):
                            details[k] = f"{len(v)} öğe" if len(v) > 8 else v
                        elif isinstance(v, float):
                            details[k] = round(v, 4)
                        else:
                            details[k] = v
            if job.error:
                details["error"] = job.error[:300]
            audit.write(
                job.project_id,
                kind=job.kind,
                title=f"{job.title} — {self._status_label(job.status)}",
                title_key=job.title_key,
                title_params=job.title_params,
                details=details,
                user_action=f"job_{job.status.value}",
            )
        except Exception:
            pass  # writing the audit is not critical — it must not break the main flow

    @staticmethod
    def _status_label(s: JobStatus) -> str:
        return {
            JobStatus.completed: "tamamlandı",
            JobStatus.failed: "başarısız",
            JobStatus.cancelled: "iptal edildi",
            JobStatus.running: "çalışıyor",
            JobStatus.queued: "kuyrukta",
        }.get(s, str(s))

    # --- SSE listeners ---
    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._listeners.setdefault(job_id, set()).add(q)
        # First frame: the current state
        job = self._jobs.get(job_id)
        if job:
            q.put_nowait(job.to_dict())
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        listeners = self._listeners.get(job_id)
        if listeners:
            listeners.discard(q)

    def _notify(self, job_id: str, done: bool = False) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return
        payload = job.to_dict()
        for q in list(self._listeners.get(job_id, ())):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass
        if done:
            # done sentinel
            for q in list(self._listeners.get(job_id, ())):
                try:
                    q.put_nowait(None)
                except asyncio.QueueFull:
                    pass


job_runner = JobRunner()
