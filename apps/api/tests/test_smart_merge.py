"""Tests for the Smart Merge DOI conflict rule.

Verifies that when both records have normalized but differing DOIs, they are
never treated as the same publication regardless of title or journal similarity:
they neither auto-merge nor enter the borderline (manual review) queue. Exercises
negative_rule_check, doi_conflict, and compute_match in services.smart_merger.
"""

import sys
from pathlib import Path

# Add apps/api to the import path (same pattern as the other tests)
_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

import pandas as pd  # noqa: E402

from services.smart_merger import (  # noqa: E402
    compute_match,
    dedup_within_source,
    doi_conflict,
    generate_candidates,
    normalize_doi,
)


def _rec(doi=None, title="deep learning bibliometric analysis", year=2020,
         surname="SMITH", pmid=None, issn=None, journal="scientometrics"):
    """A minimal record dict with the _norm_* fields that compute_match reads."""
    return {
        "_norm_doi": doi,
        "_norm_title": title,
        "_norm_year": year,
        "_norm_surname": surname,
        "_norm_pmid": pmid,
        "_norm_issn": issn,
        "_norm_journal": journal,
    }


# ── Identifier hierarchy: DOI > PMID > ISSN ───────────────────────────────
# A DEFINITE equality at a higher level overrides a conflict at a lower level.

def test_same_doi_overrides_issn_conflict():
    """REAL CASE: WoS provides the print-ISSN, Scopus the e-ISSN — in a pair with the
    same DOI an ISSN conflict cannot veto the merge (old behavior: silent duplicate)."""
    w = _rec(doi="10.1/same", issn="12345678")
    s = _rec(doi="10.1/same", issn="87654321")
    m = compute_match(w, s)
    assert m is not None and m["stage"] == "1_doi_exact"


def test_same_doi_overrides_pmid_conflict():
    w = _rec(doi="10.1/same", pmid="111")
    s = _rec(doi="10.1/same", pmid="222")
    m = compute_match(w, s)
    assert m is not None and m["stage"] == "1_doi_exact"


def test_same_pmid_overrides_issn_conflict():
    w = _rec(pmid="333", issn="11112222")
    s = _rec(pmid="333", issn="33334444")
    m = compute_match(w, s)
    assert m is not None and m["stage"] == "2_pmid_exact"


def test_issn_conflict_still_blocks_title_stages():
    """If DOI/PMID cannot be compared, an ISSN conflict still guards the title stages."""
    w = _rec(issn="11112222")
    s = _rec(issn="33334444")
    assert compute_match(w, s) is None


def test_pmid_conflict_rejects_without_doi():
    assert compute_match(_rec(pmid="1"), _rec(pmid="2")) is None


# ── compute_match: DOI is decisive ────────────────────────────────────────

def test_identical_title_but_different_doi_is_not_a_match():
    """Same title+year+surname (without a DOI, Stage 3 @0.95 would merge) but different DOI
    → NO match (None). Prevents a wrong automatic merge."""
    w = _rec(doi="10.1/aaa")
    s = _rec(doi="10.1/bbb")
    assert compute_match(w, s) is None


def test_identical_title_without_dois_still_matches_control():
    """Control: when the DOIs are removed from the same two records, the Stage 3 merge comes back
    — i.e. the only difference that triggers the rejection is the DOI conflict."""
    w = _rec(doi=None)
    s = _rec(doi=None)
    m = compute_match(w, s)
    assert m is not None
    assert m["stage"] == "3_title_year_surname"


def test_different_doi_never_enters_borderline_queue():
    """Even with titles close to the borderline range (slightly different), if the DOI
    differs the result must be None — it must not enter the uncertain pairs queue."""
    w = _rec(doi="10.1/aaa", title="machine learning citation network analysis")
    s = _rec(doi="10.1/bbb", title="machine learning citation networks analysis")
    m = compute_match(w, s)
    assert m is None


def test_same_doi_merges_stage1():
    """Regression: an equal DOI must still yield a Stage 1 (DOI exact) merge."""
    w = _rec(doi="10.1/aaa", title="alpha")
    s = _rec(doi="10.1/aaa", title="beta")  # titles differ but the DOI is equal → merge
    m = compute_match(w, s)
    assert m is not None
    assert m["stage"] == "1_doi_exact"
    assert m["confidence"] == 1.00


def test_one_side_missing_doi_can_still_match_on_title():
    """If only one side has a DOI, the DOI rule is not applied; the title match continues."""
    w = _rec(doi="10.1/aaa")
    s = _rec(doi=None)
    m = compute_match(w, s)
    assert m is not None
    assert m["stage"] == "3_title_year_surname"


# ── doi_conflict: list_borderline safety layer ────────────────────────────

def test_doi_conflict_detects_different_after_normalize():
    """Even if the prefix/case differ, there is a conflict if the DOIs are DIFFERENT after normalization."""
    assert doi_conflict("https://doi.org/10.1/AAA", "10.1/bbb") is True


def test_doi_conflict_same_after_normalize_is_false():
    """URL prefix + upper/lower case difference, same DOI after normalization → NO conflict."""
    assert doi_conflict("https://doi.org/10.1/ABC", "10.1/abc") is False


def test_doi_conflict_one_or_both_missing_is_false():
    """One side (or both sides) without a DOI → no conflict; falls through to the title match."""
    assert doi_conflict("10.1/aaa", "") is False
    assert doi_conflict(None, "10.1/bbb") is False
    assert doi_conflict(None, None) is False


def test_doi_conflict_invalid_doi_is_false():
    """Invalid values not starting with '10.' become None in normalize → no conflict."""
    assert doi_conflict("not-a-doi", "10.1/aaa") is False


# ── Generic short-title protection ────────────────────────────────────────

def test_generic_short_title_not_auto_merged():
    """'Editorial' x2 — same year/author but DIFFERENT volume+page: the journal's editor
    writes an editorial for every issue; an automatic Stage 3 merge would be WRONG."""
    w = {**_rec(title="editorial", journal="journal x"), "VL": "1", "BP": "1"}
    s = {**_rec(title="editorial", journal="journal x"), "VL": "2", "BP": "55"}
    assert compute_match(w, s) is None


def test_short_title_still_merges_via_stage4():
    """Short title + SAME journal+volume+page → Stage 4 still merges."""
    w = {**_rec(title="editorial", journal="journal x"), "VL": "7", "BP": "1"}
    s = {**_rec(title="editorial", journal="journal x"), "VL": "7", "BP": "1"}
    m = compute_match(w, s)
    assert m is not None and m["stage"] == "4_journal_vol_page"


def test_informative_title_still_stage3():
    """Informative titles keep merging at Stage 3 (regression)."""
    m = compute_match(_rec(), _rec())
    assert m is not None and m["stage"] == "3_title_year_surname"


# ── Numeric field / DOI normalize edge cases ──────────────────────────────

def test_numeric_vl_bp_float_vs_string_stage4():
    """xlsx float (100.0) vs WoS txt string ('100') — Stage 4 must not miss it."""
    w = {**_rec(title="totally different alpha", journal="chem eng journal"), "VL": "100", "BP": "55"}
    s = {**_rec(title="unrelated beta heading", journal="chem eng journal"), "VL": 100.0, "BP": 55.0}
    m = compute_match(w, s)
    assert m is not None and m["stage"] == "4_journal_vol_page"


def test_normalize_doi_prefix_variants():
    assert normalize_doi("doi:10.1234/ABC") == "10.1234/abc"
    assert normalize_doi("DOI: 10.1234/abc") == "10.1234/abc"
    assert normalize_doi("https://doi.org/https://doi.org/10.1/x") == "10.1/x"
    assert normalize_doi("doi.org/10.1/y") == "10.1/y"
    assert normalize_doi("not-a-doi") is None


# ── DOI separator folding ('_' ↔ '-') ─────────────────────────────────────
# REAL CASE: WoS indexes the same article as '10.4103/jgid.jgid_12_19', Scopus as
# '10.4103/jgid.jgid-12-19'. Without folding, doi_conflict() returned True and the
# pair was vetoed before it could even enter the borderline queue.

def test_normalize_doi_folds_underscore_to_hyphen():
    assert (normalize_doi("10.4103/jgid.jgid_12_19")
            == normalize_doi("10.4103/jgid.jgid-12-19")
            == "10.4103/jgid.jgid-12-19")


def test_doi_conflict_false_for_underscore_hyphen_variants():
    assert doi_conflict("10.4103/jgid.jgid_12_19", "10.4103/jgid.jgid-12-19") is False


def test_normalize_doi_folds_unicode_dash_family():
    """En dash / em dash / hyphen (U+2010–U+2015) also become the canonical '-'."""
    assert normalize_doi("10.4103/jgid.jgid–12–19") == "10.4103/jgid.jgid-12-19"
    assert normalize_doi("10.1000/ab‐cd") == normalize_doi("10.1000/ab-cd")
    assert doi_conflict("10.4103/jgid.jgid_12_19",
                        "10.4103/jgid.jgid–12–19") is False


def test_distinct_dois_still_conflict_after_folding():
    """Folding does not swallow real differences — distinct DOIs still conflict."""
    assert doi_conflict("10.1016/j.joi.2017.08.007", "10.1016/j.joi.2017.08.008") is True
    assert doi_conflict("10.4103/jgid.jgid_12_19", "10.4103/jgid.jgid_13_19") is True


# ── generate_candidates: identifier-first (bypasses blocking) ─────────────

def _frame(rows):
    return pd.DataFrame([{
        "_norm_doi": None, "_norm_title": "", "_norm_year": None,
        "_norm_surname": "", "_norm_pmid": None, "_norm_issn": None,
        "_norm_ut": None, "_norm_journal": "", **r,
    } for r in rows])


def test_same_doi_escapes_blocking_regression():
    """REAL CASE (39 duplicate groups): same DOI but the surname was parsed DIFFERENTLY
    ('RAHIM' vs 'ABDUL RAHIM') and/or the year differs (early-access) → the old
    blocking-only candidate generation never compared the pair. With identifier-first
    generation, a Stage 1 (DOI exact) candidate MUST be produced."""
    wos = _frame([{"_norm_doi": "10.1002/ceat.1", "_norm_title": "hydrogen fuel cell triz",
                   "_norm_year": 2024, "_norm_surname": "RAHIM"}])
    scp = _frame([{"_norm_doi": "10.1002/ceat.1", "_norm_title": "hydrogen fuel cell triz",
                   "_norm_year": 2025, "_norm_surname": "ABDUL RAHIM"}])
    cands = generate_candidates(wos, scp)
    assert len(cands) == 1
    assert cands[0][3]["stage"] == "1_doi_exact"


def test_same_pmid_escapes_blocking():
    wos = _frame([{"_norm_pmid": "12345", "_norm_title": "alpha", "_norm_year": 2020, "_norm_surname": "SMITH"}])
    scp = _frame([{"_norm_pmid": "12345", "_norm_title": "beta study", "_norm_year": 2021, "_norm_surname": "JONES"}])
    cands = generate_candidates(wos, scp)
    assert len(cands) == 1
    assert cands[0][3]["stage"] == "2_pmid_exact"


def test_blocking_pairs_not_duplicated_by_identifier_pass():
    """If the same pair comes from both the DOI index and a block, a SINGLE candidate must be produced."""
    wos = _frame([{"_norm_doi": "10.1/x", "_norm_title": "same title here",
                   "_norm_year": 2020, "_norm_surname": "SMITH"}])
    scp = _frame([{"_norm_doi": "10.1/x", "_norm_title": "same title here",
                   "_norm_year": 2020, "_norm_surname": "SMITH"}])
    cands = generate_candidates(wos, scp)
    assert len(cands) == 1


# ── dedup_within_source ───────────────────────────────────────────────────

def test_dedup_within_source_same_ut_keeps_richest():
    """REAL CASE: two ISI rows with the same WoS UT (overlapping exports).
    The fullest row must be kept."""
    df = _frame([
        {"_norm_ut": "wos:1", "TI": "Title", "AB": ""},
        {"_norm_ut": "wos:1", "TI": "Title", "AB": "An abstract makes this row richer"},
    ])
    out, removed = dedup_within_source(df)
    assert removed == 1 and len(out) == 1
    assert out.iloc[0]["AB"] != ""


def test_dedup_within_source_same_doi():
    df = _frame([
        {"_norm_doi": "10.1/dup", "TI": "A"},
        {"_norm_doi": "10.1/dup", "TI": "B"},
        {"_norm_doi": "10.1/other", "TI": "C"},
    ])
    out, removed = dedup_within_source(df)
    assert removed == 1 and len(out) == 2


def test_dedup_within_source_no_duplicates_untouched():
    df = _frame([
        {"_norm_ut": "wos:1", "_norm_doi": "10.1/a"},
        {"_norm_ut": "wos:2", "_norm_doi": "10.1/b"},
    ])
    out, removed = dedup_within_source(df)
    assert removed == 0 and len(out) == 2


def test_different_doi_blocks_stage4_journal_match():
    """Different DOI + unrelated titles (no Stage 3/5) but same journal/volume/page:
    without a DOI, Stage 4 (Journal+Vol+Pages) would merge; the DOI conflict yields None."""
    w = _rec(doi="10.1/aaa", title="alpha study one", journal="scientometrics")
    s = _rec(doi="10.1/bbb", title="totally unrelated heading two", journal="scientometrics")
    w.update({"VL": "10", "BP": "100"})
    s.update({"VL": "10", "BP": "100"})
    assert compute_match(w, s) is None
    # Control: when the DOIs are removed, the Stage 4 (Journal+Vol+Pages) merge comes back.
    w["_norm_doi"] = None
    s["_norm_doi"] = None
    m = compute_match(w, s)
    assert m is not None
    assert m["stage"] == "4_journal_vol_page"
