"""Tests for first-run sample-project seeding.

Verifies that a bundled "Simple Project" is created once on startup when the
storage directory is empty and the samples-directory env var is set, that only
recognized sample files are imported, and that seeding never repeats once the
marker exists or when projects already exist.
"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient


def _fresh_app(monkeypatch, storage: Path, samples: Path | None):
    monkeypatch.setenv("STORAGE_DIR", str(storage))
    storage.mkdir(parents=True, exist_ok=True)
    if samples is None:
        monkeypatch.delenv("BIBEXPY_SAMPLES_DIR", raising=False)
    else:
        monkeypatch.setenv("BIBEXPY_SAMPLES_DIR", str(samples))
    api_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(api_root))
    # Import config/main fresh (so the env is re-read in every test)
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models")):
            sys.modules.pop(mod, None)
    from main import app  # noqa: E402

    return app


def test_seed_creates_simple_project_once(monkeypatch, tmp_path):
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "scopus.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (samples / "savedrecs.txt").write_text("FN Clarivate\nVR 1.0\n", encoding="utf-8")
    (samples / "notes.md").write_text("ignored", encoding="utf-8")  # unknown type → not copied
    storage = tmp_path / "storage"

    # First launch (empty storage) → Simple Project + only the 2 recognized files
    app = _fresh_app(monkeypatch, storage, samples)
    with TestClient(app) as client:  # context manager → startup events run
        projects = client.get("/api/projects").json()
        assert [p["name"] for p in projects] == ["Simple Project"]
        pid = projects[0]["id"]
        files = client.get(f"/api/projects/{pid}/files").json()
        assert {f["kind"] for f in files} == {"scopus_csv", "wos_txt"}
        assert len(files) == 2

    # The user deletes the sample project...
    app2 = _fresh_app(monkeypatch, storage, samples)
    with TestClient(app2) as client:
        pid = client.get("/api/projects").json()[0]["id"]
        assert client.delete(f"/api/projects/{pid}").status_code == 204

    # ...it is NOT recreated on the next launch (.sample_seeded marker)
    app3 = _fresh_app(monkeypatch, storage, samples)
    with TestClient(app3) as client:
        assert client.get("/api/projects").json() == []


def test_no_seed_without_env(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path / "storage", None)
    with TestClient(app) as client:
        assert client.get("/api/projects").json() == []


def test_no_seed_when_projects_exist(monkeypatch, tmp_path):
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "scopus.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    storage = tmp_path / "storage"

    # First prepare a storage that already has a project created without samples
    app = _fresh_app(monkeypatch, storage, None)
    with TestClient(app) as client:
        client.post("/api/projects", json={"name": "Mine"})

    # Even if the env is set, samples are NOT added to a non-empty storage
    app2 = _fresh_app(monkeypatch, storage, samples)
    with TestClient(app2) as client:
        names = [p["name"] for p in client.get("/api/projects").json()]
        assert names == ["Mine"]
