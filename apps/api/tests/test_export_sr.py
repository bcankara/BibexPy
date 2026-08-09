"""SR (Short Reference) generation for biblioshiny-compatible exports.

biblioshiny's xlsx/csv import path does NOT run convert2df: it reads the file
raw and assumes an SR column already exists (utils.R wcTable does
``rep(M$SR, lengths(WC))`` unguarded → "differing number of rows: 0, N" when
SR is absent). Structured exports (xlsx/csv/tsv) therefore must carry SR,
built with bibliometrix's own metaTagExtraction(Field="SR") algorithm.
"""

import sys
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("BIBEXPY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from main import app
    return TestClient(app)


# ── Birim: SR formatı ve fallback zinciri ────────────────────────────────

def test_sr_format_and_source_fallback():
    from services import exporter

    df = pd.DataFrame({
        "AU": ["SMITH, J; DOE, A", "KARA, BC", "YILMAZ, E"],
        "PY": [2020.0, "2021", None],          # float / str / eksik yıl
        "J9": ["J AM CHEM SOC", "", ""],        # 2. ve 3. satır J9'suz
        "JI": ["J. Am. Chem. Soc.", "J. Doc.", ""],
        "SO": ["JOURNAL OF THE ACS", "JOURNAL OF DOCUMENTATION", "SCIENTOMETRICS"],
    })
    out = exporter.ensure_sr(df)

    assert list(out["SR"]) == [
        "SMITH J, 2020, J AM CHEM SOC",   # J9 doğrudan; 2020.0 → 2020
        "KARA BC, 2021, J Doc",            # J9 boş, JI'dan (noktalar → boşluk)
        "YILMAZ E, NA, SCIENTOMETRICS",    # J9+JI boş → SO; eksik yıl → NA
    ]
    assert "SR_FULL" in out.columns
    # Girdi frame'ine SR eklenmemiş olmalı (copy üzerinde çalışır)
    assert "SR" not in df.columns


def test_sr_duplicate_suffixes_compound_like_bibliometrix():
    """3 özdeş SR → X, X-a, X-a-b (bibliometrix'in iteratif duplicated()
    döngüsü birleşik süffiks üretir; -a, -b, -c DEĞİL)."""
    from services import exporter

    df = pd.DataFrame({
        "AU": ["SMITH, J"] * 3,
        "PY": [2020] * 3,
        "SO": ["J DOC"] * 3,
    })
    out = exporter.ensure_sr(df)
    assert list(out["SR"]) == ["SMITH J, 2020, J DOC",
                               "SMITH J, 2020, J DOC-a",
                               "SMITH J, 2020, J DOC-a-b"]
    # SR_FULL süffikssiz kalır
    assert list(out["SR_FULL"]) == ["SMITH J, 2020, J DOC"] * 3


def test_sr_preserved_if_already_present():
    from services import exporter

    df = pd.DataFrame({"AU": ["A, B"], "PY": [2020], "SO": ["X"], "SR": ["CUSTOM"]})
    out = exporter.ensure_sr(df)
    assert list(out["SR"]) == ["CUSTOM"]


def test_sr_missing_author_yields_na():
    from services import exporter

    df = pd.DataFrame({"AU": ["", None], "PY": [2020, 2021], "SO": ["X", "Y"]})
    out = exporter.ensure_sr(df)
    assert list(out["SR"]) == ["NA, 2020, X", "NA, 2021, Y"]


# ── Entegrasyon: xlsx export SR taşır, dataset değişmez ──────────────────

def test_xlsx_export_carries_sr_but_dataset_does_not(client):
    from services import analyses, dataset_io
    from services.filter_engine import _DF_CACHE

    pid = client.post("/api/projects", json={"name": "SRExport"}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "smart")
    dataset_io.atomic_write_dataset(pd.DataFrame({
        "AU": ["SMITH, J", "SMITH, J"],
        "TI": ["t1", "t2"],
        "PY": [2020, 2020],
        "SO": ["J DOC", "J DOC"],
        "WC": ["Info Sci; Comp Sci", "Info Sci"],
    }), adir / "merged.parquet")
    analyses.finalize_analysis(pid, aid)
    _DF_CACHE.clear()

    r = client.post(f"/api/projects/{pid}/export", json={"fmt": "xlsx"})
    assert r.status_code == 200, r.text
    name = r.json()["name"]
    dl = client.get(f"/api/projects/{pid}/download/exports/{name}")
    assert dl.status_code == 200

    exported = pd.read_excel(BytesIO(dl.content))
    assert "SR" in exported.columns and "SR_FULL" in exported.columns
    srs = list(exported["SR"])
    assert srs[0] == "SMITH J, 2020, J DOC" and srs[1] == "SMITH J, 2020, J DOC-a"
    assert len(set(srs)) == len(srs)  # biblioshiny satır adları için benzersiz

    # Aktif dataset'e SR sızmamalı — SR yalnız export sınırında üretilir
    on_disk = dataset_io.read_dataset(adir / "merged.parquet")
    assert "SR" not in on_disk.columns
