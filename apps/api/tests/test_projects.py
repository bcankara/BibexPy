"""Smoke tests for the project API: CRUD operations and file upload.

Covers the health check endpoint, the project create/list/get/delete
lifecycle, and uploading, listing, and deleting project files.
"""

import io
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Isolate the test storage
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    # Add the apps/api/ root to the path
    api_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(api_root))

    # Import config/main fresh (so the env is re-read in every test)
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models")):
            sys.modules.pop(mod, None)

    from main import app  # noqa: E402
    return TestClient(app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_project_lifecycle(client):
    # The list is empty when there are no projects
    assert client.get("/api/projects").json() == []

    # Create
    r = client.post("/api/projects", json={"name": "Test1", "description": "x"})
    assert r.status_code == 201
    pid = r.json()["id"]
    assert len(pid) == 12

    # List
    assert len(client.get("/api/projects").json()) == 1

    # Fetch a single project
    r = client.get(f"/api/projects/{pid}")
    assert r.status_code == 200
    assert r.json()["name"] == "Test1"

    # Delete
    r = client.delete(f"/api/projects/{pid}")
    assert r.status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_file_upload(client):
    pid = client.post("/api/projects", json={"name": "UploadTest"}).json()["id"]

    # Upload a CSV and a TXT
    files = [
        ("files", ("scopus.csv", io.BytesIO(b"col1,col2\n1,2\n"), "text/csv")),
        ("files", ("savedrecs.txt", io.BytesIO(b"FN Clarivate\nVR 1.0\n"), "text/plain")),
    ]
    r = client.post(f"/api/projects/{pid}/files", files=files)
    assert r.status_code == 200
    saved = r.json()
    assert len(saved) == 2
    kinds = {f["kind"] for f in saved}
    assert kinds == {"scopus_csv", "wos_txt"}

    # They should appear in the list
    r = client.get(f"/api/projects/{pid}/files")
    assert len(r.json()) == 2

    # Delete
    r = client.delete(f"/api/projects/{pid}/files/scopus.csv")
    assert r.status_code == 204
    assert len(client.get(f"/api/projects/{pid}/files").json()) == 1
