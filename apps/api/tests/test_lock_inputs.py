"""The input-side endpoints (upload, prepare, convert) take part in the per-project job lock.

Every endpoint that writes into a project's ``raw/`` or ``processed/`` folder runs inside
``job_runner.mutation(project_id)``:

- an exclusive job (merge, enrichment, disambiguation) that is active  -> 409 ``dataset_busy_job_running``;
- another mutating request in flight                                   -> 409 ``dataset_busy_request_running``;
- and while such a request is in flight, a merge start is refused with 409 ``dataset_busy_request_running``.

Read endpoints (list files, prepare status, download) stay available at all times.
"""

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

_SAMPLE = _API_ROOT.parents[1] / "python_pkg" / "src" / "bibexpy" / "_samples" / "simple_project"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models")):
            sys.modules.pop(mod, None)
    from main import app
    return TestClient(app)


def _jr():
    return sys.modules["jobs.runner"]


@pytest.fixture
def pid(client):
    return client.post("/api/projects", json={"name": "lock-inputs"}).json()["id"]


@pytest.fixture
def busy_job(pid):
    """A fake running exclusive job (e.g. an enrichment) registered in the job table."""
    runner = _jr().job_runner
    job = _jr().Job(id="busy-job", project_id=pid, kind="enrich_api", title="Fill",
                    status=_jr().JobStatus.running, exclusive=True, started_at=time.time())
    runner._jobs[job.id] = job
    yield job
    runner._jobs.pop(job.id, None)


def _put_raw(pid: str, name: str, data: bytes = b"x") -> Path:
    from services import storage
    raw = storage.project_dir(pid) / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    p = raw / name
    p.write_bytes(data)
    return p


def _put_processed(pid: str, name: str, data: bytes = b"x") -> Path:
    from services import storage
    proc = storage.project_dir(pid) / "processed"
    proc.mkdir(parents=True, exist_ok=True)
    p = proc / name
    p.write_bytes(data)
    return p


def _upload(client, pid: str, name: str = "a.csv", data: bytes = b"a,b\n1,2\n"):
    return client.post(f"/api/projects/{pid}/files", files=[("files", (name, data, "text/csv"))])


def _busy_detail(r) -> str:
    assert r.status_code == 409, r.text
    return r.json()["detail"]


# ── upload ───────────────────────────────────────────────────────────────

def test_upload_is_refused_while_an_exclusive_job_runs(client, pid, busy_job):
    r = _upload(client, pid)
    assert _busy_detail(r) == "dataset_busy_job_running"
    from services import storage
    assert not (storage.project_dir(pid) / "raw").exists() or not list((storage.project_dir(pid) / "raw").iterdir())


def test_upload_works_when_idle_and_releases_the_lock(client, pid):
    r = _upload(client, pid, "one.csv")
    assert r.status_code == 200, r.text
    assert [f["name"] for f in r.json()] == ["one.csv"]
    # The request registry is empty again -> a second upload goes through.
    assert not _jr().job_runner.mutation_in_flight(pid)
    assert _upload(client, pid, "two.csv").status_code == 200


def test_upload_is_refused_while_another_mutation_is_in_flight(client, pid):
    with _jr().job_runner.mutation(pid):
        assert _busy_detail(_upload(client, pid)) == "dataset_busy_request_running"
    assert _upload(client, pid).status_code == 200


def test_upload_in_one_project_does_not_block_another_project(client, pid):
    other = client.post("/api/projects", json={"name": "other"}).json()["id"]
    with _jr().job_runner.mutation(pid):
        assert _upload(client, other).status_code == 200


def test_delete_raw_file_is_refused_while_a_job_runs(client, pid, busy_job):
    p = _put_raw(pid, "keep.csv")
    r = client.delete(f"/api/projects/{pid}/files/keep.csv")
    assert _busy_detail(r) == "dataset_busy_job_running"
    assert p.exists()


def test_delete_raw_file_works_when_idle(client, pid):
    p = _put_raw(pid, "gone.csv")
    r = client.delete(f"/api/projects/{pid}/files/gone.csv")
    assert r.status_code == 204
    assert not p.exists()
    assert not _jr().job_runner.mutation_in_flight(pid)


def test_delete_missing_raw_file_still_404_and_releases_the_lock(client, pid):
    r = client.delete(f"/api/projects/{pid}/files/nope.csv")
    assert r.status_code == 404 and r.json()["detail"] == "file_not_found"
    assert not _jr().job_runner.mutation_in_flight(pid)


def test_listing_files_stays_available_while_a_job_runs(client, pid, busy_job):
    _put_raw(pid, "seen.csv")
    r = client.get(f"/api/projects/{pid}/files")
    assert r.status_code == 200 and [f["name"] for f in r.json()] == ["seen.csv"]


# ── prepare ──────────────────────────────────────────────────────────────

def test_prepare_is_refused_while_a_job_runs(client, pid, busy_job):
    _put_raw(pid, "a.csv")
    assert _busy_detail(client.post(f"/api/projects/{pid}/prepare")) == "dataset_busy_job_running"


@pytest.mark.skipif(not _SAMPLE.exists(), reason="bundled sample data not available")
def test_prepare_works_when_idle(client, pid):
    _put_raw(pid, "scopus.csv", (_SAMPLE / "scopus.csv").read_bytes())
    r = client.post(f"/api/projects/{pid}/prepare")
    assert r.status_code == 200, r.text
    assert r.json()["scopus_xlsx"] == "scopus.xlsx"
    assert not _jr().job_runner.mutation_in_flight(pid)


def test_prepare_with_no_raw_files_keeps_its_400_and_releases_the_lock(client, pid):
    r = client.post(f"/api/projects/{pid}/prepare")
    assert r.status_code == 400 and r.json()["detail"] == "no_files_to_process"
    assert not _jr().job_runner.mutation_in_flight(pid)


def test_prepare_reset_is_refused_while_a_job_runs(client, pid, busy_job):
    p = _put_processed(pid, "scopus.xlsx")
    assert _busy_detail(client.post(f"/api/projects/{pid}/prepare/reset")) == "dataset_busy_job_running"
    assert p.exists()


def test_prepare_reset_works_when_idle(client, pid):
    p = _put_processed(pid, "scopus.xlsx")
    r = client.post(f"/api/projects/{pid}/prepare/reset")
    assert r.status_code == 200 and r.json()["deleted_processed"] == 1
    assert not p.exists()


def test_delete_processed_file_is_refused_while_a_job_runs(client, pid, busy_job):
    p = _put_processed(pid, "scopus.xlsx")
    r = client.delete(f"/api/projects/{pid}/prepare/processed/scopus.xlsx")
    assert _busy_detail(r) == "dataset_busy_job_running"
    assert p.exists()


def test_delete_processed_file_works_when_idle(client, pid):
    p = _put_processed(pid, "scopus.xlsx")
    r = client.delete(f"/api/projects/{pid}/prepare/processed/scopus.xlsx")
    assert r.status_code == 204
    assert not p.exists()


def test_prepare_status_stays_available_while_a_job_runs(client, pid, busy_job):
    _put_processed(pid, "scopus.xlsx")
    r = client.get(f"/api/projects/{pid}/prepare/status")
    assert r.status_code == 200 and r.json()["scopus_xlsx"] == "scopus.xlsx"


# ── convert ──────────────────────────────────────────────────────────────

_CONVERT_CALLS = [
    ("csv-to-xlsx", {"files": ["a.csv"], "output": "out.xlsx"}),
    ("wos-to-xlsx", {"files": ["a.txt"], "output": "out.xlsx"}),
    ("xlsx-to-wos-txt", {"file": "a.xlsx"}),
    ("xlsx-to-tsv", {"file": "a.xlsx"}),
]


@pytest.mark.parametrize("endpoint,body", _CONVERT_CALLS)
def test_every_convert_post_is_refused_while_a_job_runs(client, pid, busy_job, endpoint, body):
    r = client.post(f"/api/projects/{pid}/convert/{endpoint}", json=body)
    assert _busy_detail(r) == "dataset_busy_job_running"


@pytest.mark.parametrize("endpoint,body", _CONVERT_CALLS)
def test_every_convert_post_is_refused_while_a_mutation_is_in_flight(client, pid, endpoint, body):
    with _jr().job_runner.mutation(pid):
        r = client.post(f"/api/projects/{pid}/convert/{endpoint}", json=body)
        assert _busy_detail(r) == "dataset_busy_request_running"


@pytest.mark.skipif(not _SAMPLE.exists(), reason="bundled sample data not available")
def test_convert_works_when_idle(client, pid):
    _put_raw(pid, "scopus.csv", (_SAMPLE / "scopus.csv").read_bytes())
    r = client.post(f"/api/projects/{pid}/convert/csv-to-xlsx",
                    json={"files": ["scopus.csv"], "output": "scopus.xlsx"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "scopus.xlsx"
    assert not _jr().job_runner.mutation_in_flight(pid)


def test_convert_download_and_list_stay_available_while_a_job_runs(client, pid, busy_job):
    _put_processed(pid, "scopus.xlsx", b"data")
    assert client.get(f"/api/projects/{pid}/convert/processed").status_code == 200
    assert client.get(f"/api/projects/{pid}/convert/download/scopus.xlsx").status_code == 200


# ── interplay with the merge start ───────────────────────────────────────

@pytest.mark.skipif(not _SAMPLE.exists(), reason="bundled sample data not available")
def test_merge_start_is_refused_while_an_upload_or_prepare_request_is_in_flight(client, pid):
    # Prepared data must exist, otherwise the merge start fails the readiness check first.
    _put_raw(pid, "scopus.csv", (_SAMPLE / "scopus.csv").read_bytes())
    assert client.post(f"/api/projects/{pid}/prepare").status_code == 200

    with _jr().job_runner.mutation(pid):  # stands for an upload / prepare request in flight
        r = client.post(f"/api/projects/{pid}/merge", json={})
        assert _busy_detail(r) == "dataset_busy_request_running"
    assert not _jr().job_runner.has_active_exclusive(pid)


def test_upload_is_refused_while_a_merge_job_runs(client, pid):
    runner = _jr().job_runner
    job = _jr().Job(id="merge-job", project_id=pid, kind="merge", title="Merge",
                    status=_jr().JobStatus.running, exclusive=True, started_at=time.time())
    runner._jobs[job.id] = job
    try:
        assert _busy_detail(_upload(client, pid)) == "dataset_busy_job_running"
        assert _busy_detail(client.post(f"/api/projects/{pid}/prepare")) == "dataset_busy_job_running"
    finally:
        runner._jobs.pop(job.id, None)
    assert _upload(client, pid).status_code == 200
