"""Simple, single-user job runner.

Runs background jobs as asyncio tasks with in-memory state, log, and progress
tracking, streamed live to clients over SSE. Provides per-project exclusivity to
prevent overlapping heavy jobs and a dedicated thread pool for cancellable
CPU-bound work so the rest of the app stays responsive.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import threading
import time
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Iterator, Optional

# Tracebacks of failed jobs go to the server log, never into the user-visible job log.
_LOG = logging.getLogger("bibexpy.jobs")

# Keep at most this many FINISHED jobs per project in memory (queued/running are never pruned).
MAX_FINISHED_JOBS_PER_PROJECT = 50

# Cap for the short, user-visible error message (job.error).
_MAX_ERROR_LEN = 500

# A leading snake_case code ("no_merged_data", "no_merged_data: details") is exposed as error_code.
_ERROR_CODE_RE = re.compile(r"^([a-z][a-z0-9_]*)(:|$)")


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


_ACTIVE_STATES = (JobStatus.queued, JobStatus.running)


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
    # Leading snake_case code of `error` (e.g. "no_merged_data"), or None.
    error_code: Optional[str] = None
    # One-line, translatable status for cards/banners: the frontend shows
    # t(status_key, status_params); the English `log` stays the technical log.
    status_key: Optional[str] = None
    status_params: Optional[dict[str, Any]] = None
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
            "exclusive": self.exclusive,
            "status": self.status.value,
            "progress": round(self.progress, 3),
            "log_tail": self.log[-50:],
            "status_key": self.status_key,
            "status_params": self.status_params or None,
            "result": self.result,
            "error": self.error,
            "error_code": self.error_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "created_at": self.created_at,
        }


class JobContext:
    """Control object passed to the worker function."""

    def __init__(self, job: Job, runner: "JobRunner"):
        self._job = job
        self._runner = runner

    def log(self, line: str, *, key: Optional[str] = None,
            params: Optional[dict[str, Any]] = None) -> None:
        """Append an English technical log line.

        When `key` is given, it also becomes the job's translatable one-line status
        (`status_key` + `status_params`); the frontend renders t(key, params).
        """
        self._job.log.append(line)
        if key is not None:
            self._job.status_key = key
            self._job.status_params = dict(params) if params else {}
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
        # Projects with a mutating REQUEST in flight (load -> snapshot -> write, run in the
        # threadpool). Guarded by `_mut_lock`, which also makes "check exclusive job, then
        # register" and "check mutation, then register job" atomic against each other.
        self._mutations: set[str] = set()
        self._mut_lock = threading.Lock()

    # --- CRUD ---
    def list(self, project_id: Optional[str] = None, active: bool = False) -> list[Job]:
        """Jobs newest first; `active=True` keeps only queued/running ones."""
        items = list(self._jobs.values())
        if project_id:
            items = [j for j in items if j.project_id == project_id]
        if active:
            items = [j for j in items if j.status in _ACTIVE_STATES]
        items.sort(key=lambda j: j.created_at, reverse=True)
        return items

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        """Cancel a queued/running job.

        The status flips to `cancelled` immediately (UI responsiveness), but the
        exclusivity lock is held until the asyncio task has actually finished, because
        a worker may still be flushing a partial result in its `finally` block.
        """
        job = self._jobs.get(job_id)
        if not job or job.status not in _ACTIVE_STATES:
            return False
        job.status = JobStatus.cancelled
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        self._notify(job_id, done=True)
        return True

    def _task_pending(self, job_id: str) -> bool:
        """True while the job's asyncio task exists and has not finished yet."""
        task = self._tasks.get(job_id)
        return task is not None and not task.done()

    def has_active_exclusive(self, project_id: str) -> bool:
        """Is an exclusive job of this project queued/running OR still finishing?

        Keyed on task completion rather than on `status` alone: after a cancel the status
        is already `cancelled` while the worker may still be writing its partial result.
        """
        return any(
            j.project_id == project_id and j.exclusive
            and (j.status in _ACTIVE_STATES or self._task_pending(j.id))
            for j in list(self._jobs.values())
        )

    def assert_idle(self, project_id: str) -> None:
        """Guard for endpoints that mutate the dataset or the active analysis.

        Raises HTTP 409 `dataset_busy_job_running` while an exclusive job of the project
        is queued/running or its task has not finished yet. Read endpoints must not call it.
        """
        if self.has_active_exclusive(project_id):
            from fastapi import HTTPException
            raise HTTPException(409, "dataset_busy_job_running")

    @contextmanager
    def mutation(self, project_id: str) -> Iterator[None]:
        """Wrap the WHOLE body of a dataset-/analysis-mutating endpoint (load -> snapshot -> write).

        `assert_idle` alone is check-then-act: a job could start, or a second mutation could
        interleave, between the check and the write. Inside this context

        - an exclusive job of the project that is active (or still finishing) -> 409
          `dataset_busy_job_running`;
        - another mutation of the project in flight -> 409 `dataset_busy_request_running`;
        - and `submit(exclusive=True)` is refused with 409 `dataset_busy_request_running`
          until the context exits.
        """
        from fastapi import HTTPException
        with self._mut_lock:
            if self.has_active_exclusive(project_id):
                raise HTTPException(409, "dataset_busy_job_running")
            if project_id in self._mutations:
                raise HTTPException(409, "dataset_busy_request_running")
            self._mutations.add(project_id)
        try:
            yield
        finally:
            with self._mut_lock:
                self._mutations.discard(project_id)

    def mutation_in_flight(self, project_id: str) -> bool:
        with self._mut_lock:
            return project_id in self._mutations

    def _prune(self, project_id: str) -> None:
        """Keep only the newest MAX_FINISHED_JOBS_PER_PROJECT finished jobs of a project.

        Queued/running jobs and jobs whose task has not finished are never removed.
        """
        finished = [
            j for j in list(self._jobs.values())
            if j.project_id == project_id
            and j.status not in _ACTIVE_STATES
            and not self._task_pending(j.id)
        ]
        if len(finished) <= MAX_FINISHED_JOBS_PER_PROJECT:
            return
        finished.sort(key=lambda j: j.created_at, reverse=True)
        for j in finished[MAX_FINISHED_JOBS_PER_PROJECT:]:
            self._jobs.pop(j.id, None)
            self._tasks.pop(j.id, None)
            self._listeners.pop(j.id, None)

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
        job = Job(
            id=uuid.uuid4().hex[:12], project_id=project_id, kind=kind,
            title=title, title_key=title_key, title_params=title_params,
            exclusive=exclusive,
        )
        # Check + register under the mutation lock: a mutating request that is already in
        # flight (it passed its idle check) must not be overtaken by a new exclusive job, and
        # a mutation starting right after sees this job in the table.
        with self._mut_lock:
            if exclusive and self.has_active_exclusive(project_id):
                from fastapi import HTTPException
                raise HTTPException(409, "job_already_running")
            if exclusive and project_id in self._mutations:
                from fastapi import HTTPException
                raise HTTPException(409, "dataset_busy_request_running")
            self._jobs[job.id] = job
        task = asyncio.create_task(self._run(job, worker))
        self._tasks[job.id] = task
        task.add_done_callback(lambda _t, _job=job: self._on_task_done(_job))
        self._prune(project_id)
        return job

    def _on_task_done(self, job: Job) -> None:
        """Runs once the asyncio task is finished (normally, failed or cancelled)."""
        if job.finished_at is None:
            # Cancelled before the coroutine ever started: `_run`'s finally never ran.
            job.finished_at = time.time()
            if job.status in _ACTIVE_STATES:
                job.status = JobStatus.cancelled
            self._notify(job.id, done=True)
        self._prune(job.project_id)

    @staticmethod
    def _error_message(exc: BaseException) -> str:
        """Short, user-visible message of a failed worker (no traceback)."""
        # HTTPException: use the bare `detail` ("no_merged_data"), not "409: no_merged_data".
        detail = getattr(exc, "detail", None)
        msg = str(detail) if detail is not None else str(exc)
        msg = msg.strip() or type(exc).__name__
        return msg[:_MAX_ERROR_LEN]

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
            job.error = self._error_message(e)
            m = _ERROR_CODE_RE.match(job.error)
            job.error_code = m.group(1) if m else None
            job.log.append("ERROR: " + job.error)
            # The traceback is for the server log only; it must never reach the job log.
            _LOG.exception("Job %s (%s) failed", job.id, job.kind)
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
                            if len(v) > 8:
                                # Long lists are stored as a count (JSON-safe, language-neutral).
                                details[k] = len(v)
                                details[k + "_truncated"] = True
                            else:
                                details[k] = v
                        elif isinstance(v, float):
                            details[k] = round(v, 4)
                        else:
                            details[k] = v
            if job.error:
                details["error"] = job.error[:300]
                if job.error_code:
                    details["error_code"] = job.error_code
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
            # Writing the audit is not critical — it must not break the main flow.
            _LOG.warning("Audit write failed for job %s", job.id, exc_info=True)

    @staticmethod
    def _status_label(s: JobStatus) -> str:
        return {
            JobStatus.completed: "completed",
            JobStatus.failed: "failed",
            JobStatus.cancelled: "cancelled",
            JobStatus.running: "running",
            JobStatus.queued: "queued",
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
