"""Regression tests for atomic dataset writes and corrupted-file handling.

df.to_excel truncates the target before a long write; a concurrent reader (or
a killed process) used to see a half-written merged.xlsx and every endpoint
500'd with "Excel file format cannot be determined". Writes now go to a tmp
file and are swapped in with os.replace; unreadable files surface as a clean
409 instead of a 500.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))


def test_atomic_write_excel_roundtrip(tmp_path):
    from services.filter_engine import atomic_write_excel

    p = tmp_path / "merged.xlsx"
    df = pd.DataFrame({"TI": ["a", "b"], "PY": [2020, 2021]})
    atomic_write_excel(df, p)
    assert p.exists()
    back = pd.read_excel(p)
    assert len(back) == 2
    # Geçici dosya artığı kalmamalı
    assert not list(tmp_path.glob("*.tmp~"))


def test_atomic_write_excel_replaces_existing(tmp_path):
    from services.filter_engine import atomic_write_excel

    p = tmp_path / "merged.xlsx"
    atomic_write_excel(pd.DataFrame({"TI": ["old"]}), p)
    atomic_write_excel(pd.DataFrame({"TI": ["new1", "new2"]}), p)
    assert len(pd.read_excel(p)) == 2
    assert not list(tmp_path.glob("*.tmp~"))


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("BIBEXPY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from main import app
    return TestClient(app)


def test_corrupted_dataset_returns_409_not_500(client, tmp_path):
    """Yarım/bozuk merged.xlsx → 500 yerine temiz 409 (snapshot ipucuyla)."""
    from services import analyses
    from services.filter_engine import _DF_CACHE

    pid = client.post("/api/projects", json={"name": "Corrupt"}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "smart")
    # Kesinti anını taklit et: yarım yazılmış (geçersiz) xlsx
    (adir / "merged.xlsx").write_bytes(b"\x00\x01half-written garbage")
    analyses.finalize_analysis(pid, aid)
    _DF_CACHE.clear()

    r = client.post(f"/api/projects/{pid}/filter", json={"spec": {}, "limit": 1})
    assert r.status_code == 409, r.text
    assert "unreadable" in r.json()["detail"] or "snapshot" in r.json()["detail"]
