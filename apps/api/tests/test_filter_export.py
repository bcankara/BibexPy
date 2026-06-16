"""Integration tests for the filter and export pipeline using real sample data.

Exercises filtering (year, full-text, presets), multi-format export, quality
overview downloads, merge summaries, auto-prepare on merge, and per-analysis
audit scoping against the live API.
"""

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
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from main import app
    return TestClient(app)


@pytest.fixture
def project_with_merged(client, tmp_path):
    """Sample data'dan bir proje oluştur; küçük bir XLSX'i aktif bir Smart
    analiz klasörüne (analyses/<id>/merged.xlsx) koy."""
    if not SAMPLE_DATA.exists():
        pytest.skip("Sample data yok")
    pid = client.post("/api/projects", json={"name": "FT"}).json()["id"]

    # WoS TXT'yi yükleyip XLSX'e çevir
    src = SAMPLE_DATA / "savedrecs.txt"
    with src.open("rb") as f:
        r = client.post(
            f"/api/projects/{pid}/files",
            files=[("files", (src.name, f.read(), "text/plain"))],
        )
    assert r.status_code == 200
    r = client.post(f"/api/projects/{pid}/convert/wos-to-xlsx",
                    json={"files": [src.name], "output": "data.xlsx"})
    assert r.status_code == 200

    # processed/data.xlsx → analyses/<id>/merged.xlsx (aktif Smart analizi)
    import shutil
    from services import analyses
    project_dir = tmp_path / pid
    analysis_id, adir = analyses.create_analysis(pid, "smart")
    shutil.copy2(project_dir / "processed" / "data.xlsx", adir / "merged.xlsx")
    analyses.finalize_analysis(pid, analysis_id)
    return pid


def test_filter_basic_year(client, project_with_merged):
    pid = project_with_merged
    r = client.post(f"/api/projects/{pid}/filter",
                    json={"spec": {"year": {"min": 2020}}, "limit": 5})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] >= 0
    assert "facets" in data
    assert "facets_all" in data
    assert isinstance(data["records"], list)


def test_filter_fulltext_boolean(client, project_with_merged):
    pid = project_with_merged
    r = client.post(f"/api/projects/{pid}/filter",
                    json={"spec": {"fulltext": {"query": "the OR study", "fields": ["TI", "AB"]}}, "limit": 3})
    assert r.status_code == 200


def test_filter_presets(client, project_with_merged):
    pid = project_with_merged
    # boş
    assert client.get(f"/api/projects/{pid}/filter/presets").json() == []
    # kaydet
    r = client.post(f"/api/projects/{pid}/filter/presets",
                    json={"name": "p1", "spec": {"year": {"min": 2020}}})
    assert r.status_code == 200
    # listele
    presets = client.get(f"/api/projects/{pid}/filter/presets").json()
    assert len(presets) == 1 and presets[0]["name"] == "p1"
    # sil
    assert client.delete(f"/api/projects/{pid}/filter/presets/p1").status_code == 204
    assert client.get(f"/api/projects/{pid}/filter/presets").json() == []


@pytest.mark.parametrize("fmt", ["csv", "xlsx", "bib", "ris", "tsv", "vos", "wos"])
def test_export_formats(client, project_with_merged, fmt):
    pid = project_with_merged
    r = client.post(f"/api/projects/{pid}/export", json={"fmt": fmt})
    assert r.status_code == 200, r.text
    out = r.json()
    EXT = {"wos": "txt", "vos": "txt"}  # WoS/VOS çıktıları .txt uzantılı
    assert out["name"].endswith(f".{EXT.get(fmt, fmt)}")
    assert out["size"] > 10
    # İndirilebilir mi?
    r = client.get(f"/api/projects/{pid}/download/exports/{out['name']}")
    assert r.status_code == 200


def test_export_filtered(client, project_with_merged):
    pid = project_with_merged
    # önce filtre uygula ve aynı filterda export et
    r = client.post(f"/api/projects/{pid}/export",
                    json={"fmt": "csv", "filter": {"year": {"min": 2020}}, "output_name": "filtered.csv"})
    assert r.status_code == 200
    assert r.json()["name"] == "filtered.csv"


@pytest.mark.parametrize("fmt,ext", [("csv", ".csv"), ("xlsx", ".xlsx")])
def test_quality_overview_download(client, project_with_merged, fmt, ext):
    """#6: Data Health Genel Bakış tablosu CSV/XLSX olarak indirilebilmeli."""
    pid = project_with_merged
    r = client.get(f"/api/projects/{pid}/quality/overview?fmt={fmt}")
    assert r.status_code == 200, r.text
    cd = r.headers.get("content-disposition", "")
    assert ext in cd, cd
    assert len(r.content) > 50


def test_quality_overview_bad_format(client, project_with_merged):
    pid = project_with_merged
    r = client.get(f"/api/projects/{pid}/quality/overview?fmt=pdf")
    assert r.status_code == 400


def test_merge_summary_smart(client, project_with_merged):
    """Regresyon: merge/summary, merge'li (smart) projede 500 vermemeli.
    (Yerel `method` değişkeni kaldırılınca NameError -> 500 olmuştu.)"""
    pid = project_with_merged
    r = client.get(f"/api/projects/{pid}/merge/summary")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["has_merge"] is True
    assert d["method"] == "smart"
    # borderline + analiz listesi de smart yolunda 500 vermemeli
    assert client.get(f"/api/projects/{pid}/merge/borderline").status_code == 200
    assert client.get(f"/api/projects/{pid}/merge/analyses").status_code == 200


def test_merge_auto_prepares_from_raw(client, tmp_path):
    """Birleşik akış: ham WoS TXT yüklenip Prepare YAPILMADAN merge çalışınca
    auto_prepare örtük olarak processed/wos.xlsx üretir ve analiz oluşur.
    (Job runner asenkron olduğundan servis katmanı doğrudan test edilir.)"""
    if not SAMPLE_DATA.exists():
        pytest.skip("Sample data yok")
    import asyncio
    from services import merger

    pid = client.post("/api/projects", json={"name": "AP"}).json()["id"]
    src = SAMPLE_DATA / "savedrecs.txt"
    with src.open("rb") as f:
        r = client.post(f"/api/projects/{pid}/files",
                        files=[("files", (src.name, f.read(), "text/plain"))])
    assert r.status_code == 200

    # Prepare çağrılmadı → processed/wos.xlsx henüz yok
    processed = tmp_path / pid / "processed"
    assert not (processed / "wos.xlsx").exists()

    # check_ready_to_merge artık raw-only projede 409 vermemeli
    merger.check_ready_to_merge(pid)

    # Merge'i doğrudan çalıştır — auto_prepare ilk fazda XLSX üretmeli
    class _Ctx:
        def log(self, *a, **k):
            pass

        def progress(self, *a, **k):
            pass

    result = asyncio.run(merger.run_merge(_Ctx(), pid))
    assert result.get("analysis_id")
    assert (processed / "wos.xlsx").exists()

    # Özet: merge var, taze (stale değil)
    d = client.get(f"/api/projects/{pid}/merge/summary").json()
    assert d["has_merge"] is True
    assert d.get("stale") is False


def test_audit_per_analysis_scoping(client, project_with_merged):
    """Her analizin AYRI geçmişi: analiz-içi işlemler aktif analize etiketlenir,
    proje-seviye işlemler (upload) None kalır; analiz silinince o analizin kayıtları
    gider, proje-seviye kayıtlar korunur."""
    pid = project_with_merged
    aid = client.get(f"/api/projects/{pid}/merge/analyses").json()["active_id"]
    assert aid

    # Analiz-içi işlem (export) → aktif analize etiketlenmeli
    r = client.post(f"/api/projects/{pid}/export", json={"fmt": "csv"})
    assert r.status_code == 200
    entries = client.get(f"/api/projects/{pid}/audit").json()
    exp = [e for e in entries if e["kind"] == "export"]
    assert exp and exp[-1].get("analysis_id") == aid
    # upload proje-seviye (None) olmalı
    ups = [e for e in entries if e["kind"] == "upload"]
    assert ups and ups[-1].get("analysis_id") is None

    # Analizi sil → export (analiz-etiketli) gider, upload (None) korunur
    assert client.delete(f"/api/projects/{pid}/merge/analyses/{aid}").status_code == 200
    after = client.get(f"/api/projects/{pid}/audit").json()
    assert not any(e["kind"] == "export" for e in after)
    assert any(e["kind"] == "upload" for e in after)
