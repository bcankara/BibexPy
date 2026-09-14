"""Export and Tools share one writer (services.exporter.write_dataset).

The two paths used to carry copies of the writing logic and drifted: SR
generation landed in project Export only, so spreadsheets converted in the
standalone Tools page still crashed biblioshiny, and their CR stayed in Scopus
grammar. These tests pin the shared writer in place and check that both paths
produce the same content for the same records.
"""

import io
import re
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))
_PACKAGES = _API_ROOT.parents[1] / "packages"
if str(_PACKAGES) not in sys.path:
    sys.path.insert(0, str(_PACKAGES))
    for _mod in [m for m in sys.modules if m == "bibex_core" or m.startswith("bibex_core.")]:
        sys.modules.pop(_mod, None)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("BIBEXPY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from main import app
    return TestClient(app)


SCOPUS_CR = ("ANDERSON E.W.; FORNELL C., CUSTOMER SATISFACTION, JOURNAL OF MARKETING, "
             "58, PP. 53-66, (1994); BACK K., TITLE, JOURNAL OF HOSPITALITY TOURISM "
             "RESEARCH, 27, PP. 419-435, (2003)")
WOS_COMMA_CR = "HESKETT, JL, 1994, HARVARD BUS REV, V72, P164; ZEITHAML, VA, 1996, J MARKETING, V60, P31"


def _records() -> pd.DataFrame:
    # Every cell filled: blank cells render differently after an xlsx vs a
    # parquet round-trip, which is not what these tests are about.
    return pd.DataFrame({
        "AU": ["KARA, BC", "SMITH, J"],
        "TI": ["First title", "Second title"],
        "SO": ["J DOC", "SCIENTOMETRICS"],
        "PY": [2021, 2022],
        "VL": ["12", "7"],
        "DI": ["10.1/a", "10.1/b"],
        "DT": ["Article", "Article"],
        "CR": [SCOPUS_CR, WOS_COMMA_CR],
    })


def _xlsx_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


def _tools_convert(client, target: str) -> bytes:
    r = client.post(
        "/api/tools/convert",
        files={"file": ("in.xlsx", _xlsx_bytes(_records()), "application/octet-stream")},
        data={"source_format": "xlsx", "target_format": target, "output_name": "out"},
    )
    assert r.status_code == 200, r.text
    return r.content


def _project_export(client, target: str) -> bytes:
    from services import analyses, dataset_io
    from services.filter_engine import _DF_CACHE

    pid = client.post("/api/projects", json={"name": f"Parity-{target}"}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "smart")
    dataset_io.atomic_write_dataset(_records(), adir / "merged.parquet")
    analyses.finalize_analysis(pid, aid)
    _DF_CACHE.clear()

    r = client.post(f"/api/projects/{pid}/export", json={"fmt": target})
    assert r.status_code == 200, r.text
    dl = client.get(f"/api/projects/{pid}/download/exports/{r.json()['name']}")
    assert dl.status_code == 200
    return dl.content


def _read_table(content: bytes, target: str) -> pd.DataFrame:
    if target == "xlsx":
        return pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
    sep = "\t" if target == "tsv" else ","
    return pd.read_csv(io.BytesIO(content), sep=sep, dtype=str, keep_default_na=False)


# ── The anti-drift guarantee ─────────────────────────────────────────────

def test_tools_delegates_to_shared_writer(client, monkeypatch):
    from services import exporter

    calls = []

    def fake(df, fmt, output):
        calls.append(fmt)
        Path(output).write_text("x", encoding="utf-8")

    monkeypatch.setattr(exporter, "write_dataset", fake)
    _tools_convert(client, "csv")
    assert calls == ["csv"]


# ── Tools table outputs now carry SR and WoS-grammar CR ─────────────────

@pytest.mark.parametrize("target", ["xlsx", "csv", "tsv"])
def test_tools_table_output_has_sr_and_normalized_cr(client, target):
    out = _read_table(_tools_convert(client, target), target)

    assert "SR" in out.columns and "SR_FULL" in out.columns
    assert out["SR"].str.strip().ne("").all()

    for cr in out["CR"]:
        assert not re.search(r"\(\d{4}\)\s*;", cr), cr         # no Scopus boundary left
        assert not re.search(r"^[^,;]+, [A-Z]{1,3}, \d{4},", cr), cr  # no "SURNAME, II," form
    assert out["CR"][0].startswith("ANDERSON EW, 1994, JOURNAL OF MARKETING, V58, P53")
    assert out["CR"][1].startswith("HESKETT JL, 1994")
    assert list(out["NR"]) == ["2", "2"]


# ── Same records → same content through both paths ──────────────────────

@pytest.mark.parametrize("target", ["csv", "tsv", "xlsx"])
def test_export_and_tools_tables_match(client, target):
    exported = _read_table(_project_export(client, target), target)
    converted = _read_table(_tools_convert(client, target), target)
    # The project dataset carries a stable per-row UID; a raw file does not.
    exported = exported.drop(columns=["UID"], errors="ignore")
    pd.testing.assert_frame_equal(exported, converted)


@pytest.mark.parametrize("target", ["wos", "vos", "bib", "ris"])
def test_export_and_tools_text_formats_match(client, target):
    assert _project_export(client, target) == _tools_convert(client, target)


# ── The shared writer never mutates its input ───────────────────────────

def test_write_dataset_does_not_mutate_input(tmp_path):
    from services import exporter

    df = _records()
    snapshot = df.copy()
    exporter.write_dataset(df, "xlsx", tmp_path / "out.xlsx")
    pd.testing.assert_frame_equal(df, snapshot)   # no SR/NR columns, CR untouched
