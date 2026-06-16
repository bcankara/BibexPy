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
    # Test storage'ını izole et
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    # apps/api/ köküne path ekle
    api_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(api_root))

    # config/main'i temiz import et (env'i her testte yeniden okumak için)
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
    # Boşken liste boş
    assert client.get("/api/projects").json() == []

    # Oluştur
    r = client.post("/api/projects", json={"name": "Test1", "description": "x"})
    assert r.status_code == 201
    pid = r.json()["id"]
    assert len(pid) == 12

    # Listele
    assert len(client.get("/api/projects").json()) == 1

    # Tekil getir
    r = client.get(f"/api/projects/{pid}")
    assert r.status_code == 200
    assert r.json()["name"] == "Test1"

    # Sil
    r = client.delete(f"/api/projects/{pid}")
    assert r.status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_file_upload(client):
    pid = client.post("/api/projects", json={"name": "UploadTest"}).json()["id"]

    # CSV ve TXT yükle
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

    # Listede görünsün
    r = client.get(f"/api/projects/{pid}/files")
    assert len(r.json()) == 2

    # Sil
    r = client.delete(f"/api/projects/{pid}/files/scopus.csv")
    assert r.status_code == 204
    assert len(client.get(f"/api/projects/{pid}/files").json()) == 1
