"""Tests for POST /api/system/open-folder (error paths only).

The success path launches the OS file manager, which must not happen in CI;
these tests cover input validation and the settings storage-transparency
fields instead.
"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("BIBEXPY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    sys.path.insert(0, str(_API_ROOT))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from main import app
    return TestClient(app)


def test_open_folder_rejects_unknown_target(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.post("/api/system/open-folder", json={"target": "C:\\Windows"})
    assert r.status_code == 400


def test_open_folder_unknown_project_404(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.post("/api/system/open-folder", json={"target": "project", "project_id": "nope"})
    assert r.status_code == 404


def test_settings_reports_active_storage_and_pending_restart(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    d = client.get("/api/settings").json()
    # Aktif depolama = süreçteki STORAGE_DIR (env var) → tmp/storage
    assert str(tmp_path / "storage") in d["active_storage"]
    assert d["storage_pending_restart"] is False

    # .env'e FARKLI bir klasör yaz → env var süreçte eskisini tuttuğundan
    # "restart bekliyor" bayrağı yükselmeli
    new_dir = tmp_path / "elsewhere"
    new_dir.mkdir()
    r = client.put("/api/settings", json={"updates": {"STORAGE_DIR": str(new_dir)}})
    assert r.status_code == 200
    d2 = r.json()
    assert d2["storage_pending_restart"] is True
    assert str(tmp_path / "storage") in d2["active_storage"]  # hâlâ eski aktif
