"""Integration tests for the filter and export pipeline using real sample data.

Exercises filtering (year, full-text, presets), multi-format export, quality
overview downloads, merge summaries, auto-prepare on merge, and per-analysis
audit scoping against the live API.
"""

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# Sample data: can be overridden with the BIBEXPY_SAMPLE_DATA env var (e.g. the
# python_pkg/src/bibexpy/_samples/simple_project inside the repo). Otherwise the old default.
SAMPLE_DATA = Path(
    os.environ.get("BIBEXPY_SAMPLE_DATA")
    or (Path(__file__).resolve().parents[3] / "BibexPy" / "Workspace" / "Sample Project" / "Data")
)


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
    """Create a project from the sample data; place a small XLSX into an active Smart
    analysis folder (analyses/<id>/merged.xlsx)."""
    if not SAMPLE_DATA.exists():
        pytest.skip("Sample data yok")
    pid = client.post("/api/projects", json={"name": "FT"}).json()["id"]

    # Upload the WoS TXT and convert it to XLSX
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

    # processed/data.xlsx → analyses/<id>/merged.xlsx (active Smart analysis)
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
    # empty
    assert client.get(f"/api/projects/{pid}/filter/presets").json() == []
    # save
    r = client.post(f"/api/projects/{pid}/filter/presets",
                    json={"name": "p1", "spec": {"year": {"min": 2020}}})
    assert r.status_code == 200
    # list
    presets = client.get(f"/api/projects/{pid}/filter/presets").json()
    assert len(presets) == 1 and presets[0]["name"] == "p1"
    # delete
    assert client.delete(f"/api/projects/{pid}/filter/presets/p1").status_code == 204
    assert client.get(f"/api/projects/{pid}/filter/presets").json() == []


def test_filter_apply_permanent_and_restore(client, project_with_merged):
    """PERMANENTLY applying the filter to the dataset: non-matching records are removed,
    a snapshot is taken and it can be restored from History. (Harmonization/Export
    working on filtered data relies on this endpoint.)"""
    pid = project_with_merged
    base = client.post(f"/api/projects/{pid}/filter",
                       json={"spec": {}, "limit": 1, "include_facets": True}).json()
    total = base["total"]
    assert total > 0

    # Find a year threshold that both keeps and removes records (so the test does not depend on the data)
    years = sorted(h["year"] for h in base["facets_all"]["year"]["histogram"])
    chosen = None
    for y in years:
        t = client.post(f"/api/projects/{pid}/filter",
                        json={"spec": {"year": {"min": y}}, "limit": 1,
                              "include_facets": False}).json()["total"]
        if 0 < t < total:
            chosen = (y, t)
            break
    if chosen is None:
        pytest.skip("Örnek veride kısmi eşleşen yıl eşiği yok")
    y, kept_expect = chosen

    # Empty spec → 400 (guard against an accidental no-op call)
    assert client.post(f"/api/projects/{pid}/filter/apply", json={"spec": {}}).status_code == 400

    # Apply → the dataset is permanently narrowed
    r = client.post(f"/api/projects/{pid}/filter/apply",
                    json={"spec": {"year": {"min": y}}})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["applied"] is True
    assert d["kept"] == kept_expect
    assert d["removed"] == total - kept_expect
    assert d["snapshot"]
    after = client.post(f"/api/projects/{pid}/filter",
                        json={"spec": {}, "limit": 1, "include_facets": False}).json()
    assert after["total"] == kept_expect

    # Restoring from the snapshot returns to the full count
    r = client.post(f"/api/projects/{pid}/records/restore-snapshot",
                    json={"snapshot": d["snapshot"]})
    assert r.status_code == 200, r.text
    back = client.post(f"/api/projects/{pid}/filter",
                       json={"spec": {}, "limit": 1, "include_facets": False}).json()
    assert back["total"] == total


@pytest.mark.parametrize("fmt", ["csv", "xlsx", "bib", "ris", "tsv", "vos", "wos"])
def test_export_formats(client, project_with_merged, fmt):
    pid = project_with_merged
    r = client.post(f"/api/projects/{pid}/export", json={"fmt": fmt})
    assert r.status_code == 200, r.text
    out = r.json()
    EXT = {"wos": "txt", "vos": "txt"}  # WoS/VOS outputs have a .txt extension
    assert out["name"].endswith(f".{EXT.get(fmt, fmt)}")
    assert out["size"] > 10
    # Is it downloadable?
    r = client.get(f"/api/projects/{pid}/download/exports/{out['name']}")
    assert r.status_code == 200


def test_export_filtered(client, project_with_merged):
    pid = project_with_merged
    # first apply a filter and export with the same filter
    r = client.post(f"/api/projects/{pid}/export",
                    json={"fmt": "csv", "filter": {"year": {"min": 2020}}, "output_name": "filtered.csv"})
    assert r.status_code == 200
    assert r.json()["name"] == "filtered.csv"


@pytest.mark.parametrize("fmt,ext", [("csv", ".csv"), ("xlsx", ".xlsx")])
def test_quality_overview_download(client, project_with_merged, fmt, ext):
    """#6: The Data Health Overview table must be downloadable as CSV/XLSX."""
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
    """Regression: merge/summary must not return 500 on a merged (smart) project.
    (When the local `method` variable was removed it caused a NameError -> 500.)"""
    pid = project_with_merged
    r = client.get(f"/api/projects/{pid}/merge/summary")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["has_merge"] is True
    assert d["method"] == "smart"
    # borderline + the analysis list must not return 500 on the smart path either
    assert client.get(f"/api/projects/{pid}/merge/borderline").status_code == 200
    assert client.get(f"/api/projects/{pid}/merge/analyses").status_code == 200


def test_merge_auto_prepares_from_raw(client, tmp_path):
    """Unified flow: when a raw WoS TXT is uploaded and merge runs WITHOUT Prepare,
    auto_prepare implicitly produces processed/wos.xlsx and an analysis is created.
    (Since the job runner is asynchronous, the service layer is tested directly.)"""
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

    # Prepare was not called → processed/wos.xlsx does not exist yet
    processed = tmp_path / pid / "processed"
    assert not (processed / "wos.xlsx").exists()

    # check_ready_to_merge must no longer raise 409 on a raw-only project
    merger.check_ready_to_merge(pid)

    # Run the merge directly — auto_prepare must produce the XLSX in the first phase
    class _Ctx:
        def log(self, *a, **k):
            pass

        def progress(self, *a, **k):
            pass

    result = asyncio.run(merger.run_merge(_Ctx(), pid))
    assert result.get("analysis_id")
    assert (processed / "wos.xlsx").exists()

    # Summary: a merge exists and it is fresh (not stale)
    d = client.get(f"/api/projects/{pid}/merge/summary").json()
    assert d["has_merge"] is True
    assert d.get("stale") is False


def test_audit_per_analysis_scoping(client, project_with_merged):
    """Each analysis has its OWN history: in-analysis operations are tagged with the
    active analysis, project-level operations (upload) stay None; when an analysis is
    deleted its records go away, while project-level records are preserved."""
    pid = project_with_merged
    aid = client.get(f"/api/projects/{pid}/merge/analyses").json()["active_id"]
    assert aid

    # In-analysis operation (export) → must be tagged with the active analysis
    r = client.post(f"/api/projects/{pid}/export", json={"fmt": "csv"})
    assert r.status_code == 200
    entries = client.get(f"/api/projects/{pid}/audit").json()
    exp = [e for e in entries if e["kind"] == "export"]
    assert exp and exp[-1].get("analysis_id") == aid
    # upload must be project-level (None)
    ups = [e for e in entries if e["kind"] == "upload"]
    assert ups and ups[-1].get("analysis_id") is None

    # Delete the analysis → export (analysis-tagged) goes away, upload (None) is preserved
    assert client.delete(f"/api/projects/{pid}/merge/analyses/{aid}").status_code == 200
    after = client.get(f"/api/projects/{pid}/audit").json()
    assert not any(e["kind"] == "export" for e in after)
    assert any(e["kind"] == "upload" for e in after)
