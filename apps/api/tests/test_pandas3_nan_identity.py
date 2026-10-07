"""Identity checks must treat NaN as "empty", not as a value.

pandas 3 (and pandas 2 with ``future.infer_string``) turns the ``None`` that
every normalize_* function returns for an empty field into ``NaN`` inside a
``str``-dtype column. ``NaN`` is truthy, so the old ``if not v: continue``
guards accepted it as a real identifier: in ``dedup_within_source`` every
DOI-less row of a source collapsed under one ``NaN`` key (a real user run lost
70 publications), the identity index in ``generate_candidates`` used ``NaN`` as
a match key, and ``compute_match`` read ``NaN`` as a present DOI and vetoed
DOI-less records against everything.

These tests feed REAL NaN values — not ``None`` — because ``astype(object)``
does not convert NaN back to None: it stays NaN and stays truthy.
"""

import sys
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from services.smart_merger import (  # noqa: E402
    _present,
    compute_match,
    dedup_within_source,
    generate_candidates,
)


# ── _present: the single identity test ───────────────────────────────────

@pytest.mark.parametrize("value", [None, float("nan"), np.nan, pd.NA, "", "   ", "nan", "NaN", "none", "null"])
def test_present_rejects_every_flavour_of_empty(value):
    assert _present(value) is False


@pytest.mark.parametrize("value", ["10.1000/abc", "WOS:000123", "2-s2.0-85139253808", 12345])
def test_present_accepts_real_identifiers(value):
    assert _present(value) is True


# ── dedup_within_source: the data-loss regression ────────────────────────

def _frame(rows):
    """Build the frame the way the merge pipeline really holds it.

    Dtype decides whether the defect bites: a float64 column hands out a NEW
    nan scalar on every access, so the pre-fix dict never collapsed them, while
    an object column (pandas 2) and a ``str`` column (pandas 3) both hand out
    the SAME nan singleton — and a dict looks up by identity first, so every
    identifier-less row landed on one key. Forcing object dtype reproduces the
    production condition under either pandas version.
    """
    df = pd.DataFrame(rows)
    for col in ("_norm_ut", "_norm_doi"):
        if col in df.columns:
            df[col] = pd.Series(list(df[col]), index=df.index, dtype=object)
    return df


def test_nan_identifiers_never_collapse_rows():
    """Rows whose identifiers are NaN are distinct records, not duplicates."""
    df = _frame([
        {"TI": "first paper", "_norm_ut": np.nan, "_norm_doi": np.nan},
        {"TI": "second paper", "_norm_ut": np.nan, "_norm_doi": np.nan},
        {"TI": "third paper", "_norm_ut": np.nan, "_norm_doi": np.nan},
    ])
    out, removed = dedup_within_source(df)
    assert removed == 0
    assert len(out) == 3
    assert sorted(out["TI"]) == ["first paper", "second paper", "third paper"]


def test_real_duplicates_still_collapse_next_to_nan_rows():
    """The NaN guard must not disable genuine identifier deduplication."""
    df = _frame([
        {"TI": "no doi one", "_norm_ut": np.nan, "_norm_doi": np.nan},
        {"TI": "shared", "_norm_ut": np.nan, "_norm_doi": "10.1000/a"},
        {"TI": "shared", "_norm_ut": np.nan, "_norm_doi": "10.1000/a"},
        {"TI": "no doi two", "_norm_ut": np.nan, "_norm_doi": np.nan},
    ])
    out, removed = dedup_within_source(df)
    assert removed == 1
    assert len(out) == 3
    # the two DOI-less rows both survive
    assert {"no doi one", "no doi two"} <= set(out["TI"])


def test_pre_fix_guard_would_have_destroyed_them():
    """Documents the defect itself: the old `if not v` guard keeps NaN.

    Three rows, two of them identifier-less. The pre-fix guard skips nothing
    and groups both NaN rows under ONE key — which is exactly the collapse that
    deleted DOI-less records. `_present` skips them instead, so they stay
    separate records.
    """
    values = [np.nan, np.nan, "10.1000/a"]

    pre_fix_keys = {}
    for i, v in enumerate(values):
        if not v:                      # the pre-fix guard: NaN is truthy
            continue
        pre_fix_keys.setdefault(v, []).append(i)
    # nothing was skipped, and the two NaN rows share a single key -> one of
    # them would have been dropped as a "duplicate"
    assert sum(len(v) for v in pre_fix_keys.values()) == 3
    assert any(len(rows) == 2 for rows in pre_fix_keys.values())

    fixed_keys = {}
    for i, v in enumerate(values):
        if not _present(v):            # the fix
            continue
        fixed_keys.setdefault(v, []).append(i)
    assert list(fixed_keys) == ["10.1000/a"]
    assert all(len(rows) == 1 for rows in fixed_keys.values())


# ── compute_match: NaN must not act as a present DOI ─────────────────────

def _rec(doi=None, title="resource scarcity and cognition in households",
         year=2020, surname="SMITH", pmid=None, issn=None,
         journal="journal of behavioral science"):
    return {
        "_norm_doi": doi, "_norm_title": title, "_norm_year": year,
        "_norm_surname": surname, "_norm_pmid": pmid, "_norm_issn": issn,
        "_norm_journal": journal,
    }


def test_nan_doi_does_not_veto_a_true_pair():
    """A DOI-less WoS record and the same publication from Scopus must match."""
    m = compute_match(_rec(doi=np.nan), _rec(doi="10.1000/real"))
    assert m is not None
    assert m["stage"] == "3_title_year_surname"


def test_both_sides_nan_doi_still_match_on_title():
    m = compute_match(_rec(doi=np.nan), _rec(doi=np.nan))
    assert m is not None
    assert m["stage"] == "3_title_year_surname"


def test_two_real_but_different_dois_are_still_vetoed():
    """The DOI-determinative rule stays intact — this must not regress."""
    assert compute_match(_rec(doi="10.1000/a"), _rec(doi="10.1000/b")) is None


def test_nan_pmid_does_not_veto():
    m = compute_match(_rec(doi=np.nan, pmid=np.nan), _rec(doi=np.nan, pmid="12345"))
    assert m is not None


# ── generate_candidates: NaN must never be a match key ───────────────────

def _side(rows):
    df = pd.DataFrame(rows)
    for col in ("_norm_doi", "_norm_pmid", "_norm_ut"):
        if col in df.columns:
            df[col] = pd.Series(list(df[col]), index=df.index, dtype=object)
    return df


def test_nan_identifiers_do_not_pair_unrelated_records():
    """Without the guard, every DOI-less WoS row paired with every DOI-less
    Scopus row through the identity index (and was then vetoed)."""
    wos = _side([
        {"_norm_doi": np.nan, "_norm_pmid": np.nan, "_norm_ut": np.nan,
         "_norm_title": "urban agriculture in temperate cities", "_norm_year": 2012,
         "_norm_surname": "ZHAO", "_norm_issn": None, "_norm_journal": "urban studies"},
    ])
    scp = _side([
        {"_norm_doi": np.nan, "_norm_pmid": np.nan, "_norm_ut": np.nan,
         "_norm_title": "spectrum sensing for cognitive radio networks", "_norm_year": 2015,
         "_norm_surname": "KANITKAR", "_norm_issn": None, "_norm_journal": "ieee communications"},
    ])
    assert generate_candidates(wos, scp) == []
