"""Regression tests for the dataset-integrity fixes.

Covers: the write that survives a task cancel (lock held until the thread ends), the
per-project mutation registry (request vs job and request vs request), cancel cleanup of an
unfinished merge analysis, update_record working on a copy, DELETE /projects guard, the ORCID
write-back never persisting a max_records-truncated frame, and the borderline state rollback on
snapshot restore.
"""

import asyncio
import contextlib
import json
import sys
import threading
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

from jobs.runner import JobRunner  # noqa: E402
from services import dataset_io  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    monkeypatch.setattr(JobRunner, "_write_audit", lambda self, job: None)


# ── run_uncancellable / write_dataset_uncancellable ──────────────────────

def test_cancel_during_write_waits_for_the_thread_before_the_task_ends():
    events: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def slow_write():
        started.set()
        release.wait(5)
        events.append("thread_done")
        return 7

    async def scenario():
        async def worker():
            try:
                await dataset_io.run_uncancellable(slow_write)
            finally:
                events.append("task_finalizer")

        task = asyncio.create_task(worker())
        while not started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.2)
        # The cancel was delivered, but the task must still be alive: the thread is running.
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert events == ["thread_done", "task_finalizer"]


def test_repeated_cancels_do_not_end_the_wait_early():
    started = threading.Event()
    release = threading.Event()

    def slow():
        started.set()
        release.wait(5)

    async def scenario():
        task = asyncio.create_task(dataset_io.run_uncancellable(slow))
        while not started.is_set():
            await asyncio.sleep(0.01)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0.05)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())


def test_uncancellable_returns_value_and_propagates_errors():
    async def scenario():
        assert await dataset_io.run_uncancellable(lambda a, b: a + b, 2, 3) == 5

        def boom():
            raise ValueError("write failed")

        with pytest.raises(ValueError):
            await dataset_io.run_uncancellable(boom)

    run(scenario())


def test_write_dataset_uncancellable_writes_the_file(tmp_path):
    df = pd.DataFrame({"UID": ["a", "b"], "TI": ["x", "y"]})
    target = tmp_path / "merged.parquet"
    run(dataset_io.write_dataset_uncancellable(df, target))
    assert list(dataset_io.read_dataset(target)["UID"]) == ["a", "b"]


def test_runner_lock_is_held_until_the_write_thread_finished():
    started = threading.Event()
    release = threading.Event()

    def slow_write():
        started.set()
        release.wait(5)

    async def scenario():
        runner = JobRunner()

        async def worker(ctx):
            await dataset_io.run_uncancellable(slow_write)

        job = runner.submit("p1", "enrich_api", "t", worker, exclusive=True)
        while not started.is_set():
            await asyncio.sleep(0.01)
        runner.cancel(job.id)
        await asyncio.sleep(0.2)
        # Cancelled in the UI, thread still writing: the project is NOT idle yet.
        with pytest.raises(HTTPException) as ei:
            runner.assert_idle("p1")
        assert ei.value.detail == "dataset_busy_job_running"
        release.set()
        with contextlib.suppress(asyncio.CancelledError):
            await runner._tasks[job.id]
        runner.assert_idle("p1")

    run(scenario())


# ── mutation registry ────────────────────────────────────────────────────

def test_mutation_blocks_exclusive_submit_and_releases_afterwards():
    async def scenario():
        runner = JobRunner()
        gate = asyncio.Event()

        async def worker(ctx):
            await gate.wait()

        with runner.mutation("p1"):
            with pytest.raises(HTTPException) as ei:
                runner.submit("p1", "enrich_api", "t", worker, exclusive=True)
            assert ei.value.status_code == 409
            assert ei.value.detail == "dataset_busy_request_running"
            # Other projects and non-exclusive jobs are unaffected.
            other = runner.submit("p2", "enrich_api", "t", worker, exclusive=True)
            plain = runner.submit("p1", "export", "t", worker, exclusive=False)
        job = runner.submit("p1", "enrich_api", "t", worker, exclusive=True)
        gate.set()
        for j in (job, other, plain):
            await runner._tasks[j.id]

    run(scenario())


def test_mutation_is_refused_while_an_exclusive_job_runs_and_registers_nothing():
    async def scenario():
        runner = JobRunner()
        gate = asyncio.Event()

        async def worker(ctx):
            await gate.wait()

        job = runner.submit("p1", "enrich_api", "t", worker, exclusive=True)
        with pytest.raises(HTTPException) as ei:
            with runner.mutation("p1"):
                pytest.fail("body must not run")
        assert ei.value.detail == "dataset_busy_job_running"
        assert not runner.mutation_in_flight("p1")
        gate.set()
        await runner._tasks[job.id]
        with runner.mutation("p1"):
            assert runner.mutation_in_flight("p1")

    run(scenario())


def test_two_mutations_of_a_project_do_not_overlap_and_exceptions_release():
    runner = JobRunner()
    with runner.mutation("p1"):
        with pytest.raises(HTTPException) as ei:
            with runner.mutation("p1"):
                pytest.fail("body must not run")
        assert ei.value.detail == "dataset_busy_request_running"
        with runner.mutation("p2"):  # another project is independent
            pass
    with pytest.raises(RuntimeError):
        with runner.mutation("p1"):
            raise RuntimeError("endpoint failed")
    assert not runner.mutation_in_flight("p1")
    with runner.mutation("p1"):
        pass


# ── endpoint-level tests ─────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models")):
            sys.modules.pop(mod, None)
    from main import app  # noqa: E402
    return TestClient(app)


def _jr():
    return sys.modules["jobs.runner"]


@pytest.fixture
def project(client):
    from services import analyses, dataset_io as dio

    pid = client.post("/api/projects", json={"name": "Integrity"}).json()["id"]
    analysis_id, adir = analyses.create_analysis(pid, "smart")
    df = pd.DataFrame({
        "UID": list(range(1, 7)),                       # sequential keys: row i has key i + 1
        "TI": [f"Title {i}" for i in range(6)],
        "PY": [2020, 2021, 2022, 2020, 2021, 2022],
    })
    dio.atomic_write_dataset(df, adir / "merged.parquet")
    analyses.finalize_analysis(pid, analysis_id)
    return pid, analysis_id


def test_endpoints_report_request_in_flight_as_409(client, project):
    pid, _ = project
    runner = _jr().job_runner
    with runner.mutation(pid):
        r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": ["1"]})
        assert r.status_code == 409 and r.json()["detail"] == "dataset_busy_request_running"
        r = client.post(f"/api/projects/{pid}/enrich/fill-all")
        assert r.status_code == 409 and r.json()["detail"] == "dataset_busy_request_running"
    # Released: the same delete works now.
    r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": ["1"]})
    assert r.status_code == 200 and r.json()["deleted"] == 1


def test_update_record_failed_save_leaves_the_cached_frame_untouched(client, project, monkeypatch):
    import routers.records as records_router
    from services import filter_engine

    pid, _ = project
    cached = filter_engine.load_merged(pid)
    assert cached.loc[cached["UID"] == 2, "TI"].iloc[0] == "Title 1"

    def failing_save(project_id, df):
        raise OSError("disk full")

    monkeypatch.setattr(records_router, "_save_dataset", failing_save)
    with pytest.raises(OSError):
        client.post(f"/api/projects/{pid}/records/update",
                    json={"uid": "2", "fields": {"TI": "EDITED", "NEWCOL": "x"}})
    # The shared cache holds no phantom edit and no phantom column.
    again = filter_engine.load_merged(pid)
    assert again.loc[again["UID"] == 2, "TI"].iloc[0] == "Title 1"
    assert "NEWCOL" not in again.columns
    assert "NEWCOL" not in cached.columns


def test_update_record_success_still_persists(client, project):
    pid, _ = project
    r = client.post(f"/api/projects/{pid}/records/update",
                    json={"uid": "3", "fields": {"TI": "EDITED"}})
    assert r.status_code == 200
    from services import filter_engine
    df = filter_engine.load_merged(pid)
    assert df.loc[df["UID"] == 3, "TI"].iloc[0] == "EDITED"


def test_delete_project_is_blocked_while_a_job_or_request_is_active(client, project):
    pid, _ = project
    runner = _jr().job_runner
    job = _jr().Job(id="busy1", project_id=pid, kind="enrich_api", title="Fill",
                    status=_jr().JobStatus.running, exclusive=True, started_at=time.time())
    runner._jobs[job.id] = job
    try:
        r = client.delete(f"/api/projects/{pid}")
        assert r.status_code == 409 and r.json()["detail"] == "dataset_busy_job_running"
    finally:
        runner._jobs.pop(job.id, None)
    with runner.mutation(pid):
        r = client.delete(f"/api/projects/{pid}")
        assert r.status_code == 409 and r.json()["detail"] == "dataset_busy_request_running"
    assert client.get(f"/api/projects/{pid}").status_code == 200
    assert client.delete(f"/api/projects/{pid}").status_code == 204


# ── unfinished analyses ──────────────────────────────────────────────────

def test_cancelled_merge_deletes_its_unfinished_analysis(client, project, monkeypatch):
    from services import analyses, smart_merger

    pid, good_id = project
    created: dict = {}

    async def fake_inner(ctx, project_id, progress):
        aid, adir = analyses.create_analysis(project_id, "smart")
        progress["analysis_id"] = aid
        created["id"] = aid
        (adir / "merged.parquet").write_bytes(b"partial")
        raise asyncio.CancelledError()

    monkeypatch.setattr(smart_merger, "_run_smart_merge", fake_inner)
    with pytest.raises(asyncio.CancelledError):
        run(smart_merger.run_smart_merge(None, pid))
    assert created["id"]
    assert not analyses.analysis_dir(pid, created["id"]).exists()
    # The finished analysis is untouched and still active.
    assert analyses.get_active_analysis_id(pid) == good_id


def test_finalized_analysis_is_not_deleted_when_a_late_step_fails(client, project, monkeypatch):
    from services import analyses, smart_merger

    pid, _ = project

    async def fake_inner(ctx, project_id, progress):
        aid, adir = analyses.create_analysis(project_id, "smart")
        progress["analysis_id"] = aid
        progress["finalized"] = True
        raise RuntimeError("late failure")

    monkeypatch.setattr(smart_merger, "_run_smart_merge", fake_inner)
    with pytest.raises(RuntimeError):
        run(smart_merger.run_smart_merge(None, pid))
    assert any(p.is_dir() for p in analyses.analyses_dir(pid).iterdir() if p.name.endswith("_smart"))


def test_unfinished_analysis_is_hidden_and_cannot_be_activated(client, project):
    from services import analyses

    pid, good_id = project
    orphan_id, orphan_dir = analyses.create_analysis(pid, "smart")  # no completed_at
    (orphan_dir / "merged.parquet").write_bytes(
        (analyses.analysis_dir(pid, good_id) / "merged.parquet").read_bytes())
    # A legacy meta (no `source`, no `completed_at`) holding merged data keeps working.
    legacy_dir = analyses.analyses_dir(pid) / "analysis_legacy_smart"
    legacy_dir.mkdir()
    (legacy_dir / "merged.parquet").write_bytes(
        (analyses.analysis_dir(pid, good_id) / "merged.parquet").read_bytes())
    analyses._write_meta(legacy_dir, {"id": "analysis_legacy_smart", "created_at": 1.0,
                                      "method": "smart", "label": "old"})

    listed = {m["id"] for m in client.get(f"/api/projects/{pid}/merge/analyses").json()["items"]}
    assert good_id in listed and "analysis_legacy_smart" in listed
    assert orphan_id not in listed

    r = client.post(f"/api/projects/{pid}/merge/analyses/{orphan_id}/activate")
    assert r.status_code == 404
    assert analyses.get_active_analysis_id(pid) == good_id
    r = client.post(f"/api/projects/{pid}/merge/analyses/analysis_legacy_smart/activate")
    assert r.status_code == 200
    # The orphan can still be cleaned up explicitly.
    assert client.delete(f"/api/projects/{pid}/merge/analyses/{orphan_id}").status_code == 200


# ── ORCID write-back never persists a truncated frame ────────────────────

class _Ctx:
    cancelled = False

    def __init__(self):
        self.lines: list[str] = []

    def log(self, line, **_kw):
        self.lines.append(line)

    def progress(self, _v):
        pass


def _patch_pipeline_for_orcid(monkeypatch, pipeline):
    block = {"key": "k", "auto": [], "members": [
        {"id": "m0", "records": [0, 1], "name_variants": ["Smith J"]},
        {"id": "m1", "records": [2], "name_variants": ["Smith John"]},
    ]}
    monkeypatch.setattr(pipeline, "build_author_blocks", lambda *a, **k: [block])
    monkeypatch.setattr(pipeline, "_resolve_member_orcid_sets", lambda *a, **k: [set(), set()])

    async def no_splits(*_a, **_k):
        return []

    monkeypatch.setattr(pipeline, "_compute_author_splits", no_splits)

    def fake_enrich(df, rows, *, email):
        for r in rows:
            if r in df.index:
                df.at[r, "OI"] = f"oi-{r}"
        return {"rows_filled": len(set(rows)), "dois_fetched": 1}

    monkeypatch.setattr(pipeline.orcid_mod, "enrich_rows_from_doi", fake_enrich)


def _orcid_project(client, n=10):
    from services import analyses, dataset_io as dio

    pid = client.post("/api/projects", json={"name": "Orcid"}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "smart")
    df = pd.DataFrame({
        "UID": list(range(1, n + 1)),
        "AU": [f"Smith J; Other{i} A" for i in range(n)],
        "DI": [f"10.1/{i}" for i in range(n)],
        "OI": [""] * n,
    })
    dio.atomic_write_dataset(df, adir / "merged.parquet")
    analyses.finalize_analysis(pid, aid)
    return pid, adir


def test_orcid_writeback_with_max_records_keeps_every_row(client, monkeypatch):
    from services import dataset_io as dio
    from services.disambiguation import pipeline

    pid, adir = _orcid_project(client, n=10)
    _patch_pipeline_for_orcid(monkeypatch, pipeline)
    monkeypatch.setattr(pipeline.settings, "disambiguation_orcid_persist", True)

    result = run(pipeline.run_author_disambiguation(_Ctx(), pid, "auto", max_records=3))
    assert result["orcid_rows_filled"] == 3

    out = dio.read_dataset(adir / "merged.parquet")
    assert len(out) == 10                                     # nothing truncated
    assert list(out["UID"]) == list(range(1, 11))
    assert list(out["OI"][:3]) == ["oi-0", "oi-1", "oi-2"]    # filled cells applied
    assert all(not str(v).strip() for v in out["OI"][3:])     # the rest untouched
    # The pre-write snapshot is the FULL original, not the 3-row slice.
    snap = storage_snapshot(result["snapshot"])
    assert len(snap) == 10 and all(not str(v).strip() for v in snap["OI"])


def storage_snapshot(rel: str) -> pd.DataFrame:
    from services import dataset_io as dio, storage
    return dio.read_dataset(storage.settings.storage_path / rel)


def test_orcid_writeback_without_truncation_is_unchanged(client, monkeypatch):
    from services import dataset_io as dio
    from services.disambiguation import pipeline

    pid, adir = _orcid_project(client, n=4)
    _patch_pipeline_for_orcid(monkeypatch, pipeline)
    monkeypatch.setattr(pipeline.settings, "disambiguation_orcid_persist", True)
    run(pipeline.run_author_disambiguation(_Ctx(), pid, "auto"))
    out = dio.read_dataset(adir / "merged.parquet")
    assert len(out) == 4 and list(out["OI"][:3]) == ["oi-0", "oi-1", "oi-2"]


def test_apply_filled_cells_only_fills_blank_cells_and_leaves_the_source_alone():
    from services.disambiguation import pipeline

    full = pd.DataFrame({"UID": list("abcd"), "OI": ["keep", "", None, ""]})
    full_before = full.copy(deep=True)
    part = full.head(3).copy()
    part.loc[0, "OI"] = "overwrite-attempt"
    part.loc[1, "OI"] = "new1"
    part.loc[2, "OI"] = "new2"
    part["ROR"] = ["r0", "", "r2"]
    out = pipeline._apply_filled_cells(full, part)
    assert list(out["OI"]) == ["keep", "new1", "new2", ""]
    assert out["ROR"].tolist()[0] == "r0" and out["ROR"].tolist()[2] == "r2"
    pd.testing.assert_frame_equal(full, full_before)
    assert "ROR" not in full.columns


# ── borderline state rollback on snapshot restore ────────────────────────

@pytest.fixture
def sample_client(monkeypatch, tmp_path):
    """App with startup seeding of the bundled sample project (as the borderline e2e test)."""
    monkeypatch.setenv("BIBEXPY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("BIBEXPY_SAMPLES_DIR",
                       str(_API_ROOT.parents[1] / "python_pkg" / "src" / "bibexpy" / "_samples" / "simple_project"))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from main import app
    with TestClient(app) as c:
        yield c


def test_borderline_state_is_restored_with_the_pre_accept_snapshot(sample_client):
    client = sample_client
    from services import analyses, dataset_io as dio, smart_merger

    projects = client.get("/api/projects").json()
    if not any(p["name"] == "Simple Project" for p in projects):
        pytest.skip("bundled sample project not available")
    pid = next(p["id"] for p in projects if p["name"] == "Simple Project")
    job = client.post(f"/api/projects/{pid}/merge", json={}).json()["job_id"]
    for _ in range(300):
        j = client.get(f"/api/jobs/{job}").json()
        if j["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(1)
    assert j["status"] == "completed", j.get("log_tail", [])[-5:]

    path = analyses.active_dataset_path(pid)
    adir = path.parent
    before = len(dio.read_dataset(path))
    queue = pd.read_excel(adir / "borderline_queue.xlsx")
    assert len(queue)
    pair = str(queue.iloc[0]["pair_id"])

    r = client.post(f"/api/projects/{pid}/merge/borderline/decide",
                    json={"decisions": [{"pair_id": pair, "decision": "accept"}]})
    assert r.status_code == 200 and r.json()["merged_pairs"] == 1
    snapshot = r.json()["snapshot"]
    state = json.loads((adir / "borderline_state.json").read_text(encoding="utf-8"))
    assert state[pair]["status"] == "accept"
    assert len(dio.read_dataset(path)) == before - 1

    r = client.post(f"/api/projects/{pid}/records/restore-snapshot", json={"snapshot": snapshot})
    assert r.status_code == 200
    assert len(dio.read_dataset(path)) == before
    state = json.loads((adir / "borderline_state.json").read_text(encoding="utf-8"))
    assert state[pair]["status"] == "pending"

    # The pair can be merged again after the rollback.
    r = client.post(f"/api/projects/{pid}/merge/borderline/decide",
                    json={"decisions": [{"pair_id": pair, "decision": "accept"}]})
    assert r.status_code == 200 and r.json()["merged_pairs"] == 1
    assert len(dio.read_dataset(path)) == before - 1


def test_restore_borderline_state_ignores_snapshots_without_a_state_copy(client, project):
    from services import analyses, smart_merger

    pid, aid = project
    adir = analyses.analysis_dir(pid, aid)
    (adir / "borderline_state.json").write_text(json.dumps({"p1": {"status": "accept"}}))
    snap = adir / "snapshots" / "pre_delete_x.parquet"
    snap.parent.mkdir(exist_ok=True)
    snap.write_bytes(b"x")
    assert smart_merger.restore_borderline_state(pid, snap) is False
    assert json.loads((adir / "borderline_state.json").read_text())["p1"]["status"] == "accept"


# ── Lead follow-ups: path containment, fallback selection, single-source cleanup ─────

def test_snapshot_restore_rejects_paths_outside_the_project(client, project, tmp_path):
    pid, _ = project
    outside = tmp_path / "outside.parquet"
    dataset_io.atomic_write_dataset(pd.DataFrame({"UID": ["x"], "TI": ["evil"]}), outside)
    for bad in ("../outside.parquet", str(outside), f"{pid}/../outside.parquet"):
        r = client.post(f"/api/projects/{pid}/records/restore-snapshot", json={"snapshot": bad})
        # 404 must come from the containment check, not from an unknown route.
        assert r.status_code == 404 and "snapshot_not_found" in r.text, (bad, r.status_code, r.text)
    # A real snapshot inside the project folder is still accepted.
    from services import analyses
    good = analyses.work_dir(pid) / "snapshots" / "pre_test.parquet"
    good.parent.mkdir(parents=True, exist_ok=True)
    dataset_io.atomic_write_dataset(pd.DataFrame({"UID": ["y"], "TI": ["ok"]}), good)
    rel = str(good.relative_to(dataset_io.Path(sys.modules["config"].settings.storage_path)))
    r = client.post(f"/api/projects/{pid}/records/restore-snapshot", json={"snapshot": rel})
    assert r.status_code == 200, r.text
    from services.disambiguation import pipeline
    with pytest.raises(HTTPException) as ei:
        pipeline.restore_snapshot(pid, f"{pid}/../outside.parquet")
    assert ei.value.status_code == 404


def test_active_fallback_never_picks_an_unfinished_analysis(client, project):
    from services import analyses

    pid, good_id = project
    orphan_id, orphan_dir = analyses.create_analysis(pid, "smart")  # newer, never finalized
    (orphan_dir / "merged.parquet").write_bytes(
        (analyses.analysis_dir(pid, good_id) / "merged.parquet").read_bytes())
    analyses.clear_active_analysis(pid)
    assert analyses.get_active_analysis_dir(pid).name == good_id
    assert analyses.is_unfinished(analyses.get_analysis_meta(pid, orphan_id))


def test_cancelled_single_source_merge_deletes_its_unfinished_analysis(client, project, monkeypatch):
    from services import analyses, merger

    pid, _ = project
    before = {a["id"] for a in analyses.list_analyses(pid)}

    async def boom(*a, **k):
        raise asyncio.CancelledError()

    monkeypatch.setattr(merger.dataset_io, "write_dataset_uncancellable", boom)

    class Ctx:
        cancelled = False
        def log(self, *a, **k): pass
        def progress(self, *a, **k): pass

    df = pd.DataFrame({"UID": ["a"], "TI": ["t"]})
    with pytest.raises(asyncio.CancelledError):
        run(merger._run_single_source(Ctx(), pid, df, None))
    assert {a["id"] for a in analyses.list_analyses(pid)} == before
