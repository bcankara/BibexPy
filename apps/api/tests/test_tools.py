"""Tests for the standalone format-conversion tool endpoint.

Verifies the download filename extension returned by /api/tools/convert:
WoS and VOSviewer outputs use .txt, while tsv/csv keep their own extension.
Also checks the x-target-format response header.
"""

import io
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient


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


def _xlsx_bytes() -> bytes:
    df = pd.DataFrame({
        "AU": ["Doe J; Roe R", "Smith A"],
        "TI": ["A title", "Another title"],
        "SO": ["Journal X", "Journal Y"],
        "PY": [2020, 2021],
        "DI": ["10.1/a", "10.1/b"],
    })
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


def _convert(client, target_format: str, output_name: str = "mydata"):
    return client.post(
        "/api/tools/convert",
        files={"file": ("in.xlsx", _xlsx_bytes(), "application/octet-stream")},
        data={"source_format": "xlsx", "target_format": target_format, "output_name": output_name},
    )


def _disposition_name(resp) -> str:
    cd = resp.headers.get("content-disposition", "")
    # attachment; filename="mydata.txt" (veya filename*=...)
    for part in cd.split(";"):
        part = part.strip()
        if part.lower().startswith("filename="):
            return part.split("=", 1)[1].strip().strip('"')
    return ""


@pytest.mark.parametrize("target,expected_ext", [
    ("vos", ".txt"),   # VOSviewer TSV → .txt (eskiden .vos — BUG)
    ("wos", ".txt"),   # WoS plain text → .txt (eskiden .wos — BUG)
    ("tsv", ".tsv"),   # anahtar=uzantı → değişmez
    ("csv", ".csv"),   # anahtar=uzantı → değişmez
])
def test_tools_convert_extension(client, target, expected_ext):
    r = _convert(client, target)
    assert r.status_code == 200, r.text
    name = _disposition_name(r)
    assert name.lower().endswith(expected_ext), f"{target} → {name} (beklenen {expected_ext})"
    # wos/vos için .wos/.vos İLE BİTMEMELİ
    if expected_ext == ".txt":
        assert not name.lower().endswith((".wos", ".vos")), name
    # Header de doğru target'ı bildirmeli
    assert r.headers.get("x-target-format") == target
