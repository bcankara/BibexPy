"""Integration tests for the file conversion API endpoints using real sample data.

Covers CSV-to-XLSX, WoS-to-XLSX, and XLSX-to-WoS round-trip conversions,
along with error handling for missing input files.
"""

import io
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


SAMPLE_DATA = Path(__file__).resolve().parents[3] / "BibexPy" / "Workspace" / "Sample Project" / "Data"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    api_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(api_root))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models")):
            sys.modules.pop(mod, None)
    from main import app
    return TestClient(app)


def _upload(client, pid: str, filepath: Path):
    with filepath.open("rb") as f:
        r = client.post(
            f"/api/projects/{pid}/files",
            files=[("files", (filepath.name, f.read(), "application/octet-stream"))],
        )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.skipif(not SAMPLE_DATA.exists(), reason="Sample data yok")
def test_csv_to_xlsx_with_real_scopus(client):
    pid = client.post("/api/projects", json={"name": "scp"}).json()["id"]
    _upload(client, pid, SAMPLE_DATA / "scopus.csv")

    r = client.post(
        f"/api/projects/{pid}/convert/csv-to-xlsx",
        json={"files": ["scopus.csv"], "output": "scopus.xlsx"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "scopus.xlsx"
    assert r.json()["size"] > 1000

    r = client.get(f"/api/projects/{pid}/convert/download/scopus.xlsx")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/")


@pytest.mark.skipif(not SAMPLE_DATA.exists(), reason="Sample data yok")
def test_wos_to_xlsx_with_real_wos(client):
    pid = client.post("/api/projects", json={"name": "wos"}).json()["id"]
    src = SAMPLE_DATA / "savedrecs.txt"
    if not src.exists():
        pytest.skip("savedrecs.txt yok")
    _upload(client, pid, src)

    r = client.post(
        f"/api/projects/{pid}/convert/wos-to-xlsx",
        json={"files": ["savedrecs.txt"], "output": "wos.xlsx"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "wos.xlsx"
    assert r.json()["size"] > 1000


@pytest.mark.skipif(not SAMPLE_DATA.exists(), reason="Sample data yok")
def test_xlsx_to_wos_txt_roundtrip(client):
    pid = client.post("/api/projects", json={"name": "rt"}).json()["id"]
    _upload(client, pid, SAMPLE_DATA / "savedrecs.txt")
    client.post(f"/api/projects/{pid}/convert/wos-to-xlsx",
                json={"files": ["savedrecs.txt"], "output": "rt.xlsx"})
    r = client.post(f"/api/projects/{pid}/convert/xlsx-to-wos-txt",
                    json={"file": "rt.xlsx", "output": "rt.txt"})
    assert r.status_code == 200, r.text
    assert r.json()["size"] > 100


def test_csv_to_xlsx_missing_file(client):
    pid = client.post("/api/projects", json={"name": "x"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/convert/csv-to-xlsx",
                    json={"files": ["nope.csv"]})
    assert r.status_code == 404
