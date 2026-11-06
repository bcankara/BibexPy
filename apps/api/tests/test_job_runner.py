"""Job runner: exclusivity, cancel lock, pruning, status keys, error codes, endpoint guards.

The runner tests use a fresh ``JobRunner`` inside ``asyncio.run`` so no event loop leaks
between tests. The endpoint tests put a fake *running exclusive* job into the global
``job_runner`` (the routers share that single instance) and check that every
dataset-/analysis-mutating endpoint answers ``409 dataset_busy_job_running`` while read
endpoints keep working, and that the same call succeeds once the job is gone.
"""

import asyncio
import contextlib
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

from jobs.runner import MAX_FINISHED_JOBS_PER_PROJECT, Job, JobRunner, JobStatus  # noqa: E402


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    """Runner unit tests must not write audit rows into a real storage folder."""
    monkeypatch.setattr(JobRunner, "_write_audit", lambda self, job: None)


def run(coro):
    return asyncio.run(coro)


async def _await_task(runner: JobRunner, job: Job) -> None:
    with contextlib.suppress(asyncio.CancelledError):
        await runner._tasks[job.id]


# ── Exclusivity ──────────────────────────────────────────────────────────

def test_second_exclusive_submit_is_rejected():
    async def scenario():
        runner = JobRunner()
        gate = asyncio.Event()

        async def worker(ctx):
            await gate.wait()

        first = runner.submit("p1", "enrich_api", "t", worker, exclusive=True)
        with pytest.raises(HTTPException) as ei:
            runner.submit("p1", "disambiguate", "t2", worker, exclusive=True)
        assert ei.value.status_code == 409
        assert ei.value.detail == "job_already_running"
        # Other projects and non-exclusive jobs are unaffected.
        other = runner.submit("p2", "enrich_api", "t3", worker, exclusive=True)
        plain = runner.submit("p1", "export", "t4", worker, exclusive=False)
        gate.set()
        for j in (first, other, plain):
            await _await_task(runner, j)
        # The lock is released once the task is done.
        again = runner.submit("p1", "enrich_api", "t5", worker, exclusive=True)
        await _await_task(runner, again)

    run(scenario())


def test_assert_idle_blocks_while_running_and_allows_after():
    async def scenario():
        runner = JobRunner()
        gate = asyncio.Event()

        async def worker(ctx):
            await gate.wait()

        runner.assert_idle("p1")  # nothing running yet
        job = runner.submit("p1", "merge", "t", worker, exclusive=True)
        with pytest.raises(HTTPException) as ei:
            runner.assert_idle("p1")
        assert (ei.value.status_code, ei.value.detail) == (409, "dataset_busy_job_running")
        runner.assert_idle("p2")  # a different project is idle
        gate.set()
        await _await_task(runner, job)
        runner.assert_idle("p1")

    run(scenario())


def test_non_exclusive_job_does_not_make_the_project_busy():
    async def scenario():
        runner = JobRunner()
        gate = asyncio.Event()

        async def worker(ctx):
            await gate.wait()

        job = runner.submit("p1", "export", "t", worker, exclusive=False)
        runner.assert_idle("p1")
        gate.set()
        await _await_task(runner, job)

    run(scenario())


def test_cancel_keeps_the_lock_until_the_task_finishes():
    async def scenario():
        runner = JobRunner()
        started = asyncio.Event()
        finalise = asyncio.Event()
        flushed = []

        async def worker(ctx):
            started.set()
            try:
                await asyncio.sleep(3600)
            finally:
                # Stands in for the partial-result write of a cancelled enrichment.
                await finalise.wait()
                flushed.append(True)

        job = runner.submit("p1", "enrich_api", "t", worker, exclusive=True)
        await started.wait()
        assert runner.cancel(job.id) is True
        # The status flips immediately (UI responsiveness)...
        assert job.status == JobStatus.cancelled
        assert job.id not in {j.id for j in runner.list("p1", active=True)}
        # ...but the finalisation write is still pending, so the lock must hold.
        await asyncio.sleep(0)
        assert not flushed
        assert runner.has_active_exclusive("p1")
        with pytest.raises(HTTPException) as ei:
            runner.assert_idle("p1")
        assert ei.value.detail == "dataset_busy_job_running"
        with pytest.raises(HTTPException) as ei2:
            runner.submit("p1", "enrich_api", "t2", worker, exclusive=True)
        assert ei2.value.detail == "job_already_running"

        finalise.set()
        await _await_task(runner, job)
        assert flushed == [True]
        assert job.status == JobStatus.cancelled
        assert job.finished_at is not None
        assert not runner.has_active_exclusive("p1")
        runner.assert_idle("p1")

    run(scenario())


def test_cancel_of_a_finished_job_is_refused():
    async def scenario():
        runner = JobRunner()

        async def worker(ctx):
            return 1

        job = runner.submit("p1", "k", "t", worker)
        await _await_task(runner, job)
        assert job.status == JobStatus.completed
        assert runner.cancel(job.id) is False
        assert runner.cancel("does-not-exist") is False

    run(scenario())


# ── Listing and pruning ──────────────────────────────────────────────────

def test_list_active_filter():
    async def scenario():
        runner = JobRunner()
        gate = asyncio.Event()

        async def quick(ctx):
            return 1

        async def slow(ctx):
            await gate.wait()

        done = runner.submit("p1", "k", "done", quick)
        await _await_task(runner, done)
        running = runner.submit("p1", "k", "running", slow)
        other = runner.submit("p2", "k", "other", slow)
        await asyncio.sleep(0)
        assert {j.id for j in runner.list("p1")} == {done.id, running.id}
        assert [j.id for j in runner.list("p1", active=True)] == [running.id]
        assert {j.id for j in runner.list(active=True)} == {running.id, other.id}
        gate.set()
        await _await_task(runner, running)
        await _await_task(runner, other)

    run(scenario())


def _finished(runner: JobRunner, project: str, n: int, created_at: float) -> Job:
    job = Job(id=f"{project}-{n}", project_id=project, kind="k", title="t",
              status=JobStatus.completed, created_at=created_at)
    runner._jobs[job.id] = job
    return job


def test_prune_keeps_the_newest_finished_jobs_only():
    runner = JobRunner()
    total = MAX_FINISHED_JOBS_PER_PROJECT + 10
    jobs = [_finished(runner, "p1", i, created_at=1000.0 + i) for i in range(total)]
    keep_other = _finished(runner, "p2", 0, created_at=1.0)
    running = Job(id="run-1", project_id="p1", kind="k", title="t",
                  status=JobStatus.running, created_at=1.0)  # oldest of all, but active
    runner._jobs[running.id] = running

    runner._prune("p1")

    remaining = {j.id for j in runner.list("p1")}
    assert len([j for j in runner.list("p1") if j.status == JobStatus.completed]) == MAX_FINISHED_JOBS_PER_PROJECT
    assert running.id in remaining                      # active jobs are never pruned
    newest = {j.id for j in jobs[-MAX_FINISHED_JOBS_PER_PROJECT:]}
    assert newest <= remaining                          # the newest ones survive
    assert jobs[0].id not in remaining                  # the oldest finished ones are gone
    assert keep_other.id in {j.id for j in runner.list("p2")}  # other projects untouched


def test_prune_runs_on_submit_and_on_task_completion():
    async def scenario():
        runner = JobRunner()
        for i in range(MAX_FINISHED_JOBS_PER_PROJECT + 5):
            _finished(runner, "p1", i, created_at=1000.0 + i)

        async def worker(ctx):
            return 1

        job = runner.submit("p1", "k", "t", worker)
        await _await_task(runner, job)
        await asyncio.sleep(0)  # let the done-callback run
        finished = [j for j in runner.list("p1") if j.status != JobStatus.running]
        assert len(finished) <= MAX_FINISHED_JOBS_PER_PROJECT
        assert job.id in {j.id for j in finished}

    run(scenario())


def test_job_with_a_pending_task_is_not_pruned():
    async def scenario():
        runner = JobRunner()
        gate = asyncio.Event()
        started = asyncio.Event()

        async def worker(ctx):
            started.set()
            try:
                await asyncio.sleep(3600)
            finally:
                await gate.wait()

        victim = runner.submit("p1", "k", "t", worker)
        victim.created_at = 0.0
        await started.wait()
        runner.cancel(victim.id)  # status is cancelled, the task is still finishing
        for i in range(MAX_FINISHED_JOBS_PER_PROJECT + 5):
            _finished(runner, "p1", i, created_at=1000.0 + i)
        runner._prune("p1")
        assert runner.get(victim.id) is not None
        gate.set()
        await _await_task(runner, victim)

    run(scenario())


# ── Status key / params ──────────────────────────────────────────────────

def test_status_key_and_params_in_to_dict():
    async def scenario():
        runner = JobRunner()
        seen = {}

        async def worker(ctx):
            d0 = runner.get(ctx._job.id).to_dict()
            seen["initial"] = (d0["status_key"], d0["status_params"])
            ctx.log("Looking up DOIs 3/10", key="jobs.status.doiLookup",
                    params={"done": 3, "total": 10, "found": 1})
            seen["after_key"] = runner.get(ctx._job.id).to_dict()
            ctx.log("plain technical line")  # no key: the translatable status stays
            seen["after_plain"] = runner.get(ctx._job.id).to_dict()
            ctx.log("Writing", key="jobs.status.writing")
            seen["empty_params"] = runner.get(ctx._job.id).to_dict()
            return {"ok": True}

        job = runner.submit("p1", "enrich_api", "t", worker, exclusive=True)
        await _await_task(runner, job)
        return seen, job

    seen, job = run(scenario())
    assert seen["initial"] == (None, None)
    d = seen["after_key"]
    assert d["status_key"] == "jobs.status.doiLookup"
    assert d["status_params"] == {"done": 3, "total": 10, "found": 1}
    assert d["log_tail"][-1] == "Looking up DOIs 3/10"
    p = seen["after_plain"]
    assert p["status_key"] == "jobs.status.doiLookup"
    assert p["log_tail"][-1] == "plain technical line"
    assert seen["empty_params"]["status_key"] == "jobs.status.writing"
    assert seen["empty_params"]["status_params"] is None  # nothing to interpolate
    final = job.to_dict()
    for key in ("status_key", "status_params", "exclusive", "error_code", "error"):
        assert key in final
    assert final["exclusive"] is True
    assert final["status"] == "completed"
    json.dumps(final)  # the payload is JSON-safe


# ── Failures ─────────────────────────────────────────────────────────────

async def _run_failing(exc: BaseException) -> Job:
    runner = JobRunner()

    async def worker(ctx):
        raise exc

    job = runner.submit("p1", "enrich_api", "t", worker)
    await _await_task(runner, job)
    return job


def test_http_exception_detail_becomes_error_and_code():
    job = run(_run_failing(HTTPException(409, "no_merged_data")))
    assert job.status == JobStatus.failed
    assert job.error == "no_merged_data"
    assert job.error_code == "no_merged_data"
    assert job.to_dict()["error_code"] == "no_merged_data"


@pytest.mark.parametrize("message,code", [
    ("api_quota_exhausted: openalex", "api_quota_exhausted"),
    ("no_merged_data", "no_merged_data"),
    ("Something broke badly", None),
    ("HTTP 500 from server", None),
    ("1bad_code", None),
    ("", None),
])
def test_error_code_parsing(message, code):
    job = run(_run_failing(RuntimeError(message)))
    assert job.status == JobStatus.failed
    assert job.error_code == code
    assert job.error  # never empty (falls back to the exception type name)


def test_traceback_goes_to_the_server_log_not_the_job_log(caplog):
    with caplog.at_level(logging.ERROR, logger="bibexpy.jobs"):
        job = run(_run_failing(ValueError("boom detail")))
    text = "\n".join(job.log)
    assert job.log[-1] == "ERROR: boom detail"
    assert "Traceback" not in text
    assert "File \"" not in text
    assert "Traceback" not in "\n".join(job.to_dict()["log_tail"])
    records = [r for r in caplog.records if r.name == "bibexpy.jobs"]
    assert records and records[0].exc_info is not None
    assert "ValueError" in "".join(
        logging.Formatter().formatException(records[0].exc_info)
    )


def test_long_error_message_is_truncated():
    job = run(_run_failing(RuntimeError("x" * 5000)))
    assert len(job.error) <= 500


# ── Audit details ────────────────────────────────────────────────────────

def test_audit_details_store_long_lists_as_counts(monkeypatch):
    from services import audit

    captured = {}
    # Undo the autouse `_write_audit` stub for this test, then capture the audit call.
    monkeypatch.undo()
    monkeypatch.setattr(audit, "write", lambda project_id, **kw: captured.update(kw))

    runner = JobRunner()
    job = Job(id="a1", project_id="p1", kind="disambiguate", title="Author disambiguation",
              status=JobStatus.completed, started_at=time.time() - 2, finished_at=time.time(),
              result={"candidates": list(range(12)), "uncertain": [1, 2, 3], "total": 7})
    runner._write_audit(job)

    d = captured["details"]
    assert d["candidates"] == 12 and d["candidates_truncated"] is True
    assert d["uncertain"] == [1, 2, 3] and "uncertain_truncated" not in d
    assert d["total"] == 7
    assert captured["title"] == "Author disambiguation — completed"
    assert captured["user_action"] == "job_completed"
    json.dumps(d)


# ── Endpoint guards ──────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models")):
            sys.modules.pop(mod, None)
    from main import app  # noqa: E402
    return TestClient(app)


def _jr():
    """The CURRENT ``jobs.runner`` module.

    Several test modules purge ``jobs.*`` from ``sys.modules`` to get a fresh app, so the
    instance the routers use is not necessarily the one imported at the top of this file.
    The ``client`` fixture imports ``main`` after such a purge; this returns what it loaded
    (runner instance, ``Job`` and ``JobStatus`` must all come from the same module because
    enum members of different module copies never compare equal).
    """
    return sys.modules["jobs.runner"]


def _fake_job(project_id: str, job_id: str, status_name: str, **kw):
    jr = _jr()
    return jr.Job(id=job_id, project_id=project_id, kind=kw.pop("kind", "enrich_api"),
                  title=kw.pop("title", "Fill"), status=getattr(jr.JobStatus, status_name), **kw)


@contextlib.contextmanager
def busy(project_id: str, exclusive: bool = True):
    """Put a fake running job into the shared runner for the duration of the block."""
    runner = _jr().job_runner
    job = _fake_job(project_id, "fake-busy-job", "running", exclusive=exclusive,
                    started_at=time.time())
    runner._jobs[job.id] = job
    try:
        yield job
    finally:
        runner._jobs.pop(job.id, None)
        runner._tasks.pop(job.id, None)


@pytest.fixture
def project(client):
    """A project with a finalised analysis holding a small merged dataset."""
    from services import analyses, dataset_io

    pid = client.post("/api/projects", json={"name": "JobGuards"}).json()["id"]
    analysis_id, adir = analyses.create_analysis(pid, "smart")
    df = pd.DataFrame({
        "UID": list(range(1, 7)),                       # sequential keys
        "TI": [f"Title {i}" for i in range(6)],
        "DT": ["Article", "Article", "Article", "Review", "Review", "Article"],
        "PY": [2020, 2021, 2022, 2020, 2021, 2022],
    })
    dataset_io.atomic_write_dataset(df, adir / "merged.parquet")
    analyses.finalize_analysis(pid, analysis_id)
    return pid, analysis_id


def _is_busy(resp) -> bool:
    return resp.status_code == 409 and resp.json().get("detail") == "dataset_busy_job_running"


def test_jobs_endpoint_active_filter(client):
    pid = client.post("/api/projects", json={"name": "Jobs"}).json()["id"]
    runner = _jr().job_runner
    done = _fake_job(pid, "done-job", "completed", kind="k", title="t")
    runner._jobs[done.id] = done
    try:
        with busy(pid):
            everything = client.get("/api/jobs", params={"project_id": pid}).json()
            active = client.get("/api/jobs", params={"project_id": pid, "active": "true"}).json()
        assert {j["id"] for j in everything} == {"done-job", "fake-busy-job"}
        assert [j["id"] for j in active] == ["fake-busy-job"]
        assert active[0]["exclusive"] is True
        assert {"status_key", "status_params", "error_code"} <= set(active[0])
    finally:
        runner._jobs.pop(done.id, None)


def test_records_delete_is_blocked_while_a_job_runs(client, project):
    pid, _ = project
    with busy(pid):
        r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": ["1"]})
        assert _is_busy(r), r.text
    r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": ["1"]})
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 1


def test_records_update_and_restore_are_blocked_while_a_job_runs(client, project):
    pid, _ = project
    with busy(pid):
        r = client.post(f"/api/projects/{pid}/records/update",
                        json={"uid": "2", "fields": {"TI": "Edited"}})
        assert _is_busy(r), r.text
        r = client.post(f"/api/projects/{pid}/records/restore-snapshot",
                        json={"snapshot": "whatever.parquet"})
        assert _is_busy(r), r.text
    r = client.post(f"/api/projects/{pid}/records/update",
                    json={"uid": "2", "fields": {"TI": "Edited"}})
    assert r.status_code == 200, r.text


def test_filter_apply_is_blocked_but_filter_view_is_not(client, project):
    pid, _ = project
    body = {"spec": {"doc_type": ["Review"]}}
    with busy(pid):
        assert _is_busy(client.post(f"/api/projects/{pid}/filter/apply", json=body))
        # Read endpoints must keep working while a job runs.
        view = client.post(f"/api/projects/{pid}/filter", json={"spec": {}})
        assert view.status_code == 200, view.text
        assert client.get(f"/api/projects/{pid}/filter/presets").status_code == 200
        # Presets do not mutate the dataset.
        saved = client.post(f"/api/projects/{pid}/filter/presets",
                            json={"name": "p", "spec": {"doc_type": ["Review"]}})
        assert saved.status_code == 200, saved.text
    r = client.post(f"/api/projects/{pid}/filter/apply", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["applied"] is True


def test_analysis_activate_and_delete_are_blocked_while_a_job_runs(client, project):
    from services import analyses

    pid, analysis_id = project
    second_id, second_dir = analyses.create_analysis(pid, "smart")
    (second_dir / "merged.parquet").write_bytes((analyses.analysis_dir(pid, analysis_id) / "merged.parquet").read_bytes())
    # A finished merge carries completed_at (finalize_analysis would also make it active).
    meta = analyses._read_meta(second_dir)
    meta["completed_at"] = time.time()
    analyses._write_meta(second_dir, meta)
    with busy(pid):
        r = client.post(f"/api/projects/{pid}/merge/analyses/{second_id}/activate")
        assert _is_busy(r), r.text
        r = client.delete(f"/api/projects/{pid}/merge/analyses/{second_id}")
        assert _is_busy(r), r.text
        # Listing stays available.
        assert client.get(f"/api/projects/{pid}/merge/analyses").status_code == 200
    r = client.post(f"/api/projects/{pid}/merge/analyses/{second_id}/activate")
    assert r.status_code == 200, r.text
    assert r.json()["active_id"] == second_id


def test_borderline_decide_and_disambiguation_apply_are_blocked(client, project):
    pid, _ = project
    with busy(pid):
        r = client.post(f"/api/projects/{pid}/merge/borderline/decide",
                        json={"decisions": [{"pair_id": "x", "decision": "accept"}]})
        assert _is_busy(r), r.text
        r = client.post(f"/api/projects/{pid}/disambiguate/apply",
                        json={"kind": "authors", "approved": []})
        assert _is_busy(r), r.text
        r = client.post(f"/api/projects/{pid}/disambiguate/restore", json={"snapshot": "x"})
        assert _is_busy(r), r.text
        # Reading proposals is fine.
        assert client.get(f"/api/projects/{pid}/disambiguate/proposals/authors").status_code != 409


def test_merge_start_is_exclusive_and_blocked_while_busy(client, project, monkeypatch):
    import routers.merge as merge_router

    pid, _ = project
    with busy(pid):
        r = client.post(f"/api/projects/{pid}/merge")
        assert _is_busy(r), r.text

    captured = {}

    def fake_submit(**kwargs):
        captured.update(kwargs)
        return _fake_job(pid, "merge-fake", "queued", kind=kwargs["kind"], title=kwargs["title"])

    monkeypatch.setattr(merge_router.merger, "check_ready_to_merge", lambda project_id: None)
    monkeypatch.setattr(merge_router.job_runner, "submit", fake_submit)
    r = client.post(f"/api/projects/{pid}/merge")
    assert r.status_code == 200, r.text
    assert r.json() == {"job_id": "merge-fake"}
    assert captured["kind"] == "merge"
    assert captured["exclusive"] is True
    assert captured["title"] == "Merge (Smart Merge)"


def test_a_non_exclusive_job_does_not_block_mutations(client, project):
    pid, _ = project
    with busy(pid, exclusive=False):
        r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": ["3"]})
        assert r.status_code == 200, r.text
