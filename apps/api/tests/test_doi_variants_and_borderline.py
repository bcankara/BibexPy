"""DOI rendering variants, year-tolerant blocking and real borderline merges.

Three 2.3.0 behaviours, each pinned against the case that motivated it:

* A DOI written two ways by two indexers ("10.7189/jogh.12-05057" vs
  "10.7189/JOGH.12.05057") no longer vetoes the pair, but it is NOT evidence
  either: the pair must still pass the title/year/author stages and can never
  reach Stage 1. The rule is deliberately narrow — collapsing every separator
  would equate genuinely different Physical Review papers whose volume|page
  boundary shifts ("PhysRevC.5.350", Michaud 1972, vs "PhysRevC.53.50",
  Awasthi 1996; both DOIs are registered).
* Candidate blocking spans the neighbouring years, so Stage 3's ±1-year
  tolerance can actually fire (early-access vs print year).
* Accepting an uncertain pair MERGES the two records with the merge stage's own
  field preferences instead of deleting the Scopus row, which used to drop the
  Scopus-preferred abstract, author lists and affiliations.
"""

import sys
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))
_PACKAGES = _API_ROOT.parents[1] / "packages"
if str(_PACKAGES) not in sys.path:
    sys.path.insert(0, str(_PACKAGES))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from services.smart_merger import (  # noqa: E402
    _merge_accepted_pairs,
    compute_match,
    doi_conflict,
    doi_separator_variants,
    generate_candidates,
    normalize_doi,
)


# ── DOI rendering variants ───────────────────────────────────────────────

# Real pairs measured in user corpora; each is one publication indexed twice.
SAME_WORK = [
    ("10.7189/jogh.12-05057", "10.7189/JOGH.12.05057"),
    ("10.3724/SP.J.1041.2026.0221", "10.3724/SPJ.1041.2026.0221"),
    ("10.1515/pz-2013-0001", "10.1515/pz.2013.0001"),
    ("10.21014/acta_imeko.v13i2.1753", "10.21014/actaimeko.v13i2.1753"),
    ("10.4995/vitruvio-ijats.2022.18808", "10.4995/vitruvioijats.2022.18808"),
]

# Registered, DIFFERENT publications that a loose "strip every separator"
# rule would equate — the volume|page boundary moves between digits.
DIFFERENT_WORKS = [
    ("10.1103/PhysRevC.5.350", "10.1103/PhysRevC.53.50"),
    ("10.1103/PhysRevC.3.1701", "10.1103/PhysRevC.31.701"),
    ("10.1103/PhysRevA.34.87", "10.1103/PhysRevA.3.487"),
    ("10.1103/PhysRevC.26.55", "10.1103/PhysRevC.2.655"),
]


@pytest.mark.parametrize("a,b", SAME_WORK)
def test_rendering_variants_are_recognised(a, b):
    assert doi_separator_variants(normalize_doi(a), normalize_doi(b))
    assert doi_conflict(a, b) is False


@pytest.mark.parametrize("a,b", DIFFERENT_WORKS)
def test_digit_boundary_shifts_stay_vetoed(a, b):
    assert not doi_separator_variants(normalize_doi(a), normalize_doi(b))
    assert doi_conflict(a, b) is True


@pytest.mark.parametrize("a,b", [
    ("10.1000/abc-", "10.1000/abc"),          # separator added at the edge
    ("10.1000/a--b", "10.1000/a-b"),          # doubled separator
    ("10.1000/abc", "10.2000/abc"),           # different registrant prefix
    ("10.1000/abc.1", "10.1000/abd.1"),       # different core characters
])
def test_other_unsafe_shapes_stay_vetoed(a, b):
    assert not doi_separator_variants(normalize_doi(a), normalize_doi(b))


def _rec(doi, title, year=2022, surname="SMITH"):
    return {
        "_norm_doi": normalize_doi(doi), "_norm_title": title, "_norm_year": year,
        "_norm_surname": surname, "_norm_pmid": None, "_norm_issn": None,
        "_norm_journal": "journal of global health",
    }


def test_variant_lifts_the_veto_but_is_never_stage_one():
    m = compute_match(_rec("10.7189/jogh.12-05057", "resource scarcity and child health outcomes"),
                      _rec("10.7189/JOGH.12.05057", "resource scarcity and child health outcomes"))
    assert m is not None
    assert m["stage"] == "3_title_year_surname"
    assert m["confidence"] < 1.0
    assert "DOI yazım farkı" in m["reason"]


def test_variant_with_different_titles_does_not_match():
    assert compute_match(_rec("10.7189/jogh.12-05057", "resource scarcity and child health outcomes"),
                         _rec("10.7189/JOGH.12.05057", "completely unrelated work on bird migration")) is None


def test_identical_dois_are_still_stage_one():
    m = compute_match(_rec("10.1000/abc", "a"), _rec("10.1000/ABC", "b"))
    assert m["stage"] == "1_doi_exact"


# ── year-tolerant blocking ───────────────────────────────────────────────

def _side(rows):
    df = pd.DataFrame(rows)
    for col in ("_norm_doi", "_norm_pmid", "_norm_ut"):
        df[col] = pd.Series(list(df[col]), index=df.index, dtype=object)
    return df


def test_adjacent_year_pair_becomes_a_candidate():
    """Early-access 2014 in WoS vs print 2015 in Scopus, no DOI on either side:
    exact-year blocking never compared them, so Stage 3's ±1 year never fired."""
    common = {"_norm_doi": None, "_norm_pmid": None, "_norm_ut": None,
              "_norm_title": "multi bit decision cooperative spectrum sensing cognitive radio",
              "_norm_surname": "ZHAO", "_norm_issn": None, "_norm_journal": "ieee wpmc"}
    wos = _side([{**common, "_norm_year": 2014}])
    scp = _side([{**common, "_norm_year": 2015}])
    cands = generate_candidates(wos, scp)
    assert len(cands) == 1
    assert cands[0][3]["stage"] == "3_title_year_surname"


def test_blocking_does_not_reach_two_years():
    common = {"_norm_doi": None, "_norm_pmid": None, "_norm_ut": None,
              "_norm_title": "multi bit decision cooperative spectrum sensing cognitive radio",
              "_norm_surname": "ZHAO", "_norm_issn": None, "_norm_journal": "ieee wpmc"}
    wos = _side([{**common, "_norm_year": 2013}])
    scp = _side([{**common, "_norm_year": 2015}])
    assert generate_candidates(wos, scp) == []


# ── accepted borderline pairs are MERGED ────────────────────────────────

def _dataset():
    return pd.DataFrame([
        {"UID": "u1", "UT": "WOS:1", "DB": "ISI", "TI": "Scarcity and attention",
         "PY": 2016, "AU": "Smith J", "AB": "", "C1": "WoS address", "DI": ""},
        {"UID": "u2", "UT": "2-s2.0-1", "DB": "SCOPUS", "TI": "Scarcity and Attention",
         "PY": 2016, "AU": "Smith J.; Doe A.", "AB": "Scopus abstract", "C1": "Scopus address", "DI": ""},
        {"UID": "u3", "UT": "WOS:2", "DB": "ISI", "TI": "Unrelated", "PY": 2019,
         "AU": "Roe R", "AB": "x", "C1": "y", "DI": ""},
    ])


def _pair(pid="p1", **over):
    base = {"pair_id": pid, "confidence": 0.85, "wos_ut": "WOS:1", "scp_ut": "2-s2.0-1",
            "wos_doi": "", "scp_doi": "", "wos_title": "Scarcity and attention",
            "scp_title": "Scarcity and Attention", "wos_year": 2016, "scp_year": 2016}
    base.update(over)
    return base


def test_accept_merges_fields_instead_of_deleting():
    out, outcomes, conflicts = _merge_accepted_pairs(_dataset(), [_pair()], set())
    assert outcomes == {"p1": "merged"}
    assert len(out) == 2                                   # one row consumed
    row = out[out["UT"] == "WOS:1"].iloc[0]
    assert row["UID"] == "u1"                              # WoS row and UID survive
    assert row["DB"] == "BIBEXPY_SMART"
    assert row["AB"] == "Scopus abstract"                  # Scopus-preferred, was lost before
    assert row["AU"] == "Smith J.; Doe A."                 # Scopus-preferred author list
    assert row["C1"] == "Scopus address"
    assert "2-s2.0-1" not in set(out["UT"])
    assert any(c["field"] == "C1" for c in conflicts)      # conflict is logged


def test_reaccepting_is_idempotent():
    out, outcomes, _ = _merge_accepted_pairs(_dataset(), [_pair()], {"p1"})
    assert outcomes == {"p1": "unresolved:already_accepted"}
    assert len(out) == 3


def test_missing_wos_row_never_deletes_the_scopus_row():
    df = _dataset()[lambda d: d["UT"] != "WOS:1"].reset_index(drop=True)
    out, outcomes, _ = _merge_accepted_pairs(df, [_pair()], set())
    assert outcomes == {"p1": "unresolved:wos_missing"}
    assert "2-s2.0-1" in set(out["UT"])                    # nothing lost


def test_missing_scopus_row_is_a_noop():
    df = _dataset()[lambda d: d["UT"] != "2-s2.0-1"].reset_index(drop=True)
    out, outcomes, _ = _merge_accepted_pairs(df, [_pair()], set())
    assert outcomes == {"p1": "noop_scp_missing"}
    assert len(out) == len(df)


def test_one_row_is_never_merged_twice_in_a_batch():
    """Two uncertain pairs can share a WoS record; only the more confident one
    merges, the other stays pending instead of double-merging."""
    df = _dataset()
    df.loc[len(df)] = {"UID": "u4", "UT": "2-s2.0-2", "DB": "SCOPUS", "TI": "Scarcity and attention",
                       "PY": 2016, "AU": "Smith J", "AB": "dup", "C1": "z", "DI": ""}
    pairs = [_pair("p1", confidence=0.90), _pair("p2", confidence=0.80, scp_ut="2-s2.0-2")]
    out, outcomes, _ = _merge_accepted_pairs(df, pairs, set())
    assert outcomes["p1"] == "merged"
    assert outcomes["p2"] == "unresolved:row_already_merged"
    assert "2-s2.0-2" in set(out["UT"])                    # the second Scopus row is kept


# ── end to end through the API ───────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("BIBEXPY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("BIBEXPY_SAMPLES_DIR",
                       str(_API_ROOT.parents[1] / "python_pkg" / "src" / "bibexpy" / "_samples" / "simple_project"))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as c:
        yield c


def test_accept_keeps_dataset_statistic_and_summary_consistent(client):
    import time

    from services import analyses, dataset_io

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

    path = analyses.active_dataset_path(pid)
    before = len(dataset_io.read_dataset(path))
    queue = pd.read_excel(path.parent / "borderline_queue.xlsx")
    assert len(queue), "the sample merge must produce uncertain pairs to review"
    pid_first = str(queue.iloc[0]["pair_id"])

    r = client.post(f"/api/projects/{pid}/merge/borderline/decide",
                    json={"decisions": [{"pair_id": pid_first, "decision": "accept"}]})
    assert r.status_code == 200
    merged = r.json()["merged_pairs"]
    assert merged == 1

    after = len(dataset_io.read_dataset(path))
    assert after == before - merged
    stat = pd.read_excel(path.parent / "Statistic.xlsx", sheet_name="General Stats").iloc[0]
    assert int(stat["Total Records"]) == after
    summary = client.get(f"/api/projects/{pid}/merge/summary").json()
    assert summary["general"]["total_records"] == after
