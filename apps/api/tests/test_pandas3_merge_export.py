"""pandas 2 / pandas 3 parity of Smart Merge field merging and the exporters.

Covers: a merge column order that does not depend on the hash seed, a float CR/NR column
normalised without version-specific warnings, RIS/BibTeX output free of the text "nan" when
a numeric column (PG/VL/IS) holds a float NaN, and a conflict log that does not report
'154.0' (WoS after an xlsx round trip) against '154' (Scopus) as a conflict.
"""

import os
import subprocess
import sys
import warnings
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))
_PACKAGES = _API_ROOT.parents[1] / "packages"
if str(_PACKAGES) not in sys.path:
    sys.path.insert(0, str(_PACKAGES))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from services import exporter  # noqa: E402
from services.bibtex_writer import write_bibtex  # noqa: E402
from services.ris_writer import write_ris  # noqa: E402
from services.smart_merger import merge_pair_with_preferences  # noqa: E402


# ── ordered union of the merge columns ───────────────────────────────────

_SNIPPET = (
    "import sys, pandas as pd\n"
    f"sys.path.insert(0, {str(_API_ROOT)!r})\n"
    f"sys.path.insert(0, {str(_PACKAGES)!r})\n"
    "from services import pdcompat\n"
    "a = pd.DataFrame(columns=['TI','AU','PY','UT','DB','AB'])\n"
    "b = pd.DataFrame(columns=['TI','EID','PY','AU','SN','DB_Original'])\n"
    "print(','.join(pdcompat.ordered_union(a.columns, b.columns)))\n"
)


def _run_with_seed(seed: str) -> str:
    env = dict(os.environ, PYTHONHASHSEED=seed)
    out = subprocess.run([sys.executable, "-c", _SNIPPET], env=env, capture_output=True,
                         text=True, check=True)
    return out.stdout.strip()


def test_ordered_union_is_independent_of_the_hash_seed():
    first, second = _run_with_seed("1"), _run_with_seed("2")
    assert first == second == "TI,AU,PY,UT,DB,AB,EID,SN,DB_Original"


def test_merge_source_uses_the_ordered_union():
    src = (_API_ROOT / "services" / "smart_merger.py").read_text(encoding="utf-8")
    assert "list(set(list(wos_df.columns)" not in src
    assert "pdcompat.ordered_union(wos_df.columns, scp_df.columns)" in src


# ── conflict detection ───────────────────────────────────────────────────

_COLS = ["VL", "BP", "TI"]


def _fields(conflicts):
    return [c["field"] for c in conflicts]


def test_numerically_equal_volume_is_not_a_conflict_but_is_still_merged():
    merged, conflicts = merge_pair_with_preferences(
        "p1", {"VL": "154.0", "TI": "A title"}, {"VL": "154", "TI": "A title"}, _COLS)
    assert "VL" not in _fields(conflicts)
    assert merged["VL"] == "154.0"        # the chosen value is the same as before (WoS wins)


def test_different_volume_is_still_a_conflict():
    _, conflicts = merge_pair_with_preferences("p1", {"VL": "154.0"}, {"VL": "155"}, _COLS)
    assert _fields(conflicts) == ["VL"]
    _, conflicts = merge_pair_with_preferences("p1", {"VL": 154.5}, {"VL": "154"}, _COLS)
    assert _fields(conflicts) == ["VL"]


def test_text_differences_are_still_conflicts_and_case_is_ignored():
    _, conflicts = merge_pair_with_preferences(
        "p1", {"TI": "One", "BP": "12"}, {"TI": "Two", "BP": "12.00"}, _COLS)
    assert _fields(conflicts) == ["TI"]
    _, conflicts = merge_pair_with_preferences("p1", {"TI": "ONE"}, {"TI": "one"}, _COLS)
    assert conflicts == []


def test_float_volume_from_a_float_column_matches_integer_text():
    _, conflicts = merge_pair_with_preferences("p1", {"VL": np.float64(154.0)}, {"VL": "154"}, _COLS)
    assert conflicts == []


# ── no "nan" text in exports ─────────────────────────────────────────────

def _nan_frame():
    return pd.DataFrame({
        "AU": ["Smith J; Doe A", np.nan],
        "TI": ["A title", "Second title"],
        "SO": ["Journal", np.nan],
        "PY": [2020, 2021],
        "VL": [np.nan, 12.0],
        "IS": [np.nan, np.nan],
        "PG": [np.nan, np.nan],
        "DE": [np.nan, "alpha; beta"],
        "DT": ["Article", np.nan],
    })


def _nan_free(text: str) -> bool:
    return not any(line.strip().lower().endswith("nan") or "  - nan" in line.lower()
                   or "{nan}" in line.lower() for line in text.splitlines())


def test_ris_has_no_nan_text(tmp_path):
    out = write_ris(_nan_frame(), tmp_path / "x.ris")
    text = out.read_text(encoding="utf-8")
    assert "nan" not in text.lower()
    assert "TY  - JOUR" in text and "KW  - alpha" in text
    assert "SP  -" not in text and "AU  - nan" not in text


def test_bibtex_has_no_nan_text(tmp_path):
    out = write_bibtex(_nan_frame(), tmp_path / "x.bib")
    text = out.read_text(encoding="utf-8")
    assert "nan" not in text.lower()
    assert "pages" not in text and "title = {A title}" in text


def test_wos_txt_export_has_no_nan_text(tmp_path):
    out = tmp_path / "x.txt"
    exporter.write_dataset(_nan_frame(), "wos", out)
    assert _nan_free(out.read_text(encoding="utf-8"))


# ── CR / NR float columns ────────────────────────────────────────────────

def test_cr_nr_float_columns_normalise_without_warnings():
    df = pd.DataFrame({"CR": [np.nan, np.nan], "NR": [np.nan, 0.0], "TI": ["a", "b"]})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = exporter._normalize_cr_for_table(df)
    assert out["CR"].isna().all()           # nothing to normalise: empty CR stays empty


def test_cr_normalisation_with_text_still_counts_references():
    cr = "Smith J., 2019, J Info, 12, pp. 1-10; Doe A., 2018, Sci, 3, pp. 4-9"
    df = pd.DataFrame({"CR": [cr, np.nan], "NR": [np.nan, np.nan]})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = exporter._normalize_cr_for_table(df)
    assert pd.isna(out.loc[1, "CR"]) or out.loc[1, "CR"] == ""
    assert int(out.loc[0, "NR"]) >= 1
    assert df["CR"].iloc[0] == cr           # the input frame is never mutated


# ── bundled sample: merge result, conflict log and exports ───────────────

_SAMPLE = _API_ROOT.parents[1] / "python_pkg" / "src" / "bibexpy" / "_samples" / "simple_project"


@pytest.fixture
def sample_merge(monkeypatch, tmp_path):
    import time
    monkeypatch.setenv("BIBEXPY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("BIBEXPY_SAMPLES_DIR", str(_SAMPLE))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as client:
        projects = client.get("/api/projects").json()
        if not any(p["name"] == "Simple Project" for p in projects):
            pytest.skip("bundled sample project not available")
        pid = next(p["id"] for p in projects if p["name"] == "Simple Project")
        job = client.post(f"/api/projects/{pid}/merge", json={}).json()["job_id"]
        for _ in range(300):
            j = client.get(f"/api/jobs/{job}").json()
            if j["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(1)
        assert j["status"] == "completed", j.get("log_tail", [])[-5:]
        yield client, pid, j["result"]


def test_sample_merge_counts_and_conflict_log(sample_merge):
    from services import analyses
    client, pid, result = sample_merge
    assert (result["merged_count"], result["matched_pairs"], result["borderline_count"]) == (3735, 422, 20)
    adir = analyses.active_dataset_path(pid).parent
    log = pd.read_excel(adir / "conflict_log.xlsx")
    vl = log[log["field"] == "VL"]
    equal_numbers = vl["wos_value"].astype(str).str.replace(r"\.0+$", "", regex=True) \
        == vl["scopus_value"].astype(str).str.replace(r"\.0+$", "", regex=True)
    assert not equal_numbers.any()


def test_sample_exports_contain_no_nan_text(sample_merge, tmp_path):
    from services import filter_engine
    _, pid, _ = sample_merge
    df = filter_engine.load_merged(pid)
    ris = write_ris(df, tmp_path / "s.ris").read_text(encoding="utf-8")
    bib = write_bibtex(df, tmp_path / "s.bib").read_text(encoding="utf-8")
    exporter.write_dataset(df, "wos", tmp_path / "s.txt")
    wos = (tmp_path / "s.txt").read_text(encoding="utf-8")
    for name, text in (("ris", ris), ("bib", bib), ("wos", wos)):
        bad = [ln for ln in text.splitlines()
               if ln.strip().lower() == "nan" or ln.lower().rstrip().endswith(" - nan")
               or ln.lower().rstrip().endswith("= {nan},") or ln.lower().endswith("{nan}")]
        assert not bad, (name, bad[:3])
