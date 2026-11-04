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


def test_storage_dir_change_applies_live(monkeypatch, tmp_path):
    """When the storage folder is saved it must become active IMMEDIATELY — no restart."""
    client = _client(monkeypatch, tmp_path)
    d = client.get("/api/settings").json()
    # Active storage = STORAGE_DIR in the process (env var) → tmp/storage
    assert str(tmp_path / "storage") in d["active_storage"]
    assert d["storage_pending_restart"] is False

    # Switch to the new folder → must apply live (env var + reload)
    new_dir = tmp_path / "elsewhere"
    r = client.put("/api/settings", json={"updates": {"STORAGE_DIR": str(new_dir)}})
    assert r.status_code == 200
    d2 = r.json()
    assert d2["storage_pending_restart"] is False
    assert str(new_dir) in d2["active_storage"]

    # A project created from now on must land in the NEW folder
    pid = client.post("/api/projects", json={"name": "LiveSwitch"}).json()["id"]
    assert (new_dir / pid / "meta.json").exists()
    # This project is not in the old folder
    assert not (tmp_path / "storage" / pid).exists()


def test_storage_dir_unwritable_rejected(monkeypatch, tmp_path):
    """A folder that cannot be created is rejected with 400; the active storage does not change."""
    client = _client(monkeypatch, tmp_path)
    # Create a FILE and try to use it as a folder → mkdir fails
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    r = client.put("/api/settings", json={"updates": {"STORAGE_DIR": str(blocker)}})
    assert r.status_code == 400
    d = client.get("/api/settings").json()
    assert str(tmp_path / "storage") in d["active_storage"]  # unchanged
