"""Token-based document-type filtering.

Web of Science writes composite document types ("Article; Early Access",
"Article; Proceedings Paper", ...). The WoS interface counts those as articles,
so a ``doc_type: ["Article"]`` selection must match them. The rule is "token OR
whole-string": a row matches when ANY ';'-separated token of its DT equals a
selected value (case-insensitive) OR its whole DT string does, so a saved preset
that stored "Article; Early Access" keeps matching its rows.

The facet is built on the same tokenizer, and one invariant ties the two
together: filtering by exactly an option the facet offers returns a total equal
to that option's count.

Expected values here are written out by hand from ``CORPUS`` rather than derived
with the production helpers, so the tests cannot agree with a bug by sharing it.
"""

import csv
import random
import sys
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from services.filter_engine import (  # noqa: E402
    _dt_tokens,
    apply_filter,
    compute_facets,
)


# ── Corpus ───────────────────────────────────────────────────────────────
# Row numbers in the comments are 1-based positions in CORPUS.

CORPUS = [
    "Article", "Article", "Article", "Article", "Article",    # 1-5
    "Article; Early Access", "Article; Early Access", "Article; Early Access",  # 6-8
    "Article; Proceedings Paper", "Article; Proceedings Paper",                 # 9-10
    "Article; Book Chapter",                                  # 11
    "article",                                                # 12 lower case
    "ARTICLE; early access",                                  # 13 other case
    "Article;Early Access",                                   # 14 no space after ';'
    "Article; Article",                                       # 15 token repeated
    " Article ; ",                                            # 16 padding, empty token
    "Review", "Review",                                       # 17-18
    "Review; Early Access",                                   # 19
    "Proceedings Paper",                                      # 20
    "", "",                                                   # 21-22 empty
    None,                                                     # 23 missing
    np.nan,                                                   # 24 missing (NaN)
    "nan",                                                    # 25 literal "nan"
]

# 0-based positions
ARTICLE_ROWS = [*range(0, 16)]                  # rows 1-16: every row with an Article token
EARLY_ACCESS_ROWS = [5, 6, 7, 12, 13, 18]       # rows 6-8, 13, 14, 19
PROCEEDINGS_ROWS = [8, 9, 19]                   # rows 9, 10, 20
BOOK_CHAPTER_ROWS = [10]                        # row 11
REVIEW_ROWS = [16, 17, 18]                      # rows 17-19
EMPTY_ROWS = [20, 21, 22, 23, 24]               # rows 21-25


def make_df(dts=None) -> pd.DataFrame:
    dts = list(CORPUS if dts is None else dts)
    return pd.DataFrame({
        "UID": [f"r{i}" for i in range(len(dts))],
        "TI": [f"title {i}" for i in range(len(dts))],
        "DT": dts,
    })


def matched_rows(df: pd.DataFrame, values) -> list[int]:
    """Positions (0-based) of the rows a doc_type selection keeps."""
    out = apply_filter(df, {"doc_type": values})
    return [int(u[1:]) for u in out["UID"]]


# ── Tokenizer ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("Article", ["Article"]),
    ("Article; Early Access", ["Article", "Early Access"]),
    ("Article;Early Access", ["Article", "Early Access"]),
    ("  Article ;  Book Chapter  ", ["Article", "Book Chapter"]),
    ("a;;b", ["a", "b"]),
    ("; ;", []),
    ("", []),
    ("   ", []),
    (None, []),
    (float("nan"), []),
    (pd.NA, []),
    ("nan", []),
    ("Article; nan", ["Article"]),
])
def test_dt_tokens(value, expected):
    assert _dt_tokens(value) == expected


# ── Filter ───────────────────────────────────────────────────────────────

def test_article_matches_plain_and_composite_types():
    df = make_df()
    assert matched_rows(df, ["Article"]) == ARTICLE_ROWS
    # The three WoS composites called out in the bug report, plus plain Article.
    kept = apply_filter(df, {"doc_type": ["Article"]})["DT"].tolist()
    for composite in ("Article; Early Access", "Article; Proceedings Paper", "Article; Book Chapter"):
        assert composite in kept
    assert "Article" in kept
    # Nothing without an Article token sneaks in.
    assert not any("Review" == d or d == "Proceedings Paper" for d in kept)


def test_other_tokens_of_a_composite_are_selectable_too():
    df = make_df()
    assert matched_rows(df, ["Early Access"]) == EARLY_ACCESS_ROWS
    assert matched_rows(df, ["Proceedings Paper"]) == PROCEEDINGS_ROWS
    assert matched_rows(df, ["Book Chapter"]) == BOOK_CHAPTER_ROWS


def test_legacy_preset_with_composite_value_still_matches():
    """A preset saved before tokenization holds the whole composite string."""
    df = make_df()
    # Rows 6-8 verbatim + row 13 ("ARTICLE; early access": case-insensitive whole string).
    assert matched_rows(df, ["Article; Early Access"]) == [5, 6, 7, 12]
    # The whole-string half is exact, not fuzzy: "Article;Early Access" (row 14)
    # is a different whole string and is NOT picked up by it.
    assert 13 not in matched_rows(df, ["Article; Early Access"])
    assert matched_rows(df, ["Article; Proceedings Paper"]) == [8, 9]


@pytest.mark.parametrize("spelling", ["Article", "article", "ARTICLE", "aRtIcLe", "  Article  "])
def test_case_and_padding_insensitive(spelling):
    assert matched_rows(make_df(), [spelling]) == ARTICLE_ROWS


def test_multiple_selected_values_are_ored():
    df = make_df()
    assert matched_rows(df, ["Review", "Book Chapter"]) == sorted(REVIEW_ROWS + BOOK_CHAPTER_ROWS)
    # Overlapping selections do not duplicate rows.
    assert matched_rows(df, ["Article", "Early Access"]) == sorted(set(ARTICLE_ROWS + EARLY_ACCESS_ROWS))
    # Mixing a token with a legacy whole-string value is also an OR.
    assert matched_rows(df, ["Review", "Article; Proceedings Paper"]) == sorted(REVIEW_ROWS + [8, 9])


def test_empty_dt_is_never_matched():
    df = make_df()
    for sel in (["Article"], ["Review", "Early Access"], ["nan"], ["NAN", "None"], [""], ["", "  "]):
        assert not set(matched_rows(df, sel)) & set(EMPTY_ROWS), sel
    # A selection made only of blanks matches nothing at all.
    assert matched_rows(df, [""]) == []
    assert matched_rows(df, ["   ", ""]) == []
    # A blank selected next to a real value is ignored, not matched against empty rows.
    assert matched_rows(df, ["", "Review"]) == REVIEW_ROWS


def test_no_doc_type_selection_is_a_noop():
    df = make_df()
    assert len(apply_filter(df, {"doc_type": []})) == len(df)
    assert len(apply_filter(df, {})) == len(df)


def test_missing_dt_column_is_a_noop():
    df = make_df().drop(columns=["DT"])
    assert len(apply_filter(df, {"doc_type": ["Article"]})) == len(df)


def test_unknown_value_matches_nothing():
    assert matched_rows(make_df(), ["Editorial Material"]) == []


def test_substring_of_a_token_does_not_match():
    """Token equality, not substring: "Art" must not match "Article"."""
    assert matched_rows(make_df(), ["Art"]) == []
    assert matched_rows(make_df(), ["Early"]) == []


def test_language_and_db_filters_stay_whole_string():
    """LA and DB keep using _apply_in unchanged: no tokenization there."""
    df = pd.DataFrame({
        "UID": ["a", "b", "c"],
        "LA": ["English", "English; French", "French"],
        "DB": ["WoS", "WoS; Scopus", "Scopus"],
    })
    assert apply_filter(df, {"language": ["English"]})["UID"].tolist() == ["a"]
    assert apply_filter(df, {"language": ["english; french"]})["UID"].tolist() == ["b"]
    assert apply_filter(df, {"db_source": ["WoS"]})["UID"].tolist() == ["a"]
    assert apply_filter(df, {"db_source": ["Scopus"]})["UID"].tolist() == ["c"]


# ── Facet ────────────────────────────────────────────────────────────────

def test_facet_shape_counts_and_order():
    facet = compute_facets(make_df())["doc_type"]
    assert facet == [
        {"value": "Article", "count": 16},
        {"value": "Early Access", "count": 6},
        {"value": "Proceedings Paper", "count": 3},
        {"value": "Review", "count": 3},
        {"value": "Book Chapter", "count": 1},
    ]
    for opt in facet:
        assert set(opt) == {"value", "count"}
        assert isinstance(opt["value"], str) and isinstance(opt["count"], int)


def test_facet_counts_a_repeated_token_once_per_record():
    df = make_df(["Article; Article; ARTICLE", "Article"])
    assert compute_facets(df)["doc_type"] == [{"value": "Article", "count": 2}]


def test_facet_keys_on_upper_case_and_shows_most_frequent_spelling():
    df = make_df(["article", "ARTICLE", "Article", "Article", "Early Access", "early access"])
    assert compute_facets(df)["doc_type"] == [
        {"value": "Article", "count": 4},       # 2x "Article" beats 1x "article" / 1x "ARTICLE"
        {"value": "Early Access", "count": 2},  # 1:1 tie -> deterministic (sorted) pick
    ]


def test_facet_spelling_tie_is_deterministic():
    df = make_df(["article", "ARTICLE"])
    first = compute_facets(df)["doc_type"]
    second = compute_facets(make_df(["ARTICLE", "article"]))["doc_type"]
    assert first == second == [{"value": "ARTICLE", "count": 2}]


def test_facet_never_offers_empty_or_nan():
    facet = compute_facets(make_df())["doc_type"]
    values = [o["value"].strip().upper() for o in facet]
    assert "" not in values and "NAN" not in values and "NONE" not in values


def test_facet_is_capped_at_top_20():
    df = make_df([f"Type{i:02d}" for i in range(25)] + ["Type00"] * 3)
    facet = compute_facets(df)["doc_type"]
    assert len(facet) == 20
    assert facet[0] == {"value": "Type00", "count": 4}


# ── Facet / filter invariant ─────────────────────────────────────────────

def assert_facet_matches_filter(df: pd.DataFrame) -> int:
    facet = compute_facets(df)["doc_type"]
    for opt in facet:
        got = len(apply_filter(df, {"doc_type": [opt["value"]]}))
        assert got == opt["count"], f"{opt['value']!r}: facet says {opt['count']}, filter returns {got}"
    return len(facet)


def test_invariant_on_corpus():
    assert assert_facet_matches_filter(make_df()) == 5


def test_invariant_with_top_cap_and_ties():
    types = [f"Type{i:02d}" for i in range(25)]
    dts = types + [f"{t}; Early Access" for t in types[:10]] + ["Type03; type03"] * 2
    assert assert_facet_matches_filter(make_df(dts)) == 20


@pytest.mark.parametrize("seed", range(8))
def test_invariant_on_random_messy_corpora(seed):
    """Random mix of case, padding, repeats, empty tokens and missing cells."""
    rng = random.Random(seed)
    pool = ["Article", "Review", "Early Access", "Proceedings Paper", "Book Chapter",
            "Editorial Material", "Letter", "Note", "Data Paper", "Retracted Publication"]

    def cell():
        roll = rng.random()
        if roll < 0.06:
            return rng.choice([None, np.nan, "", "  ", "nan", ";", " ; "])
        parts = [rng.choice(pool) for _ in range(rng.choice([1, 1, 2, 3]))]
        parts = [rng.choice([p, p.upper(), p.lower()]) for p in parts]
        parts = [(" " * rng.randint(0, 2)) + p + (" " * rng.randint(0, 2)) for p in parts]
        return rng.choice(["; ", ";", " ;  "]).join(parts) + rng.choice(["", "", ";"])

    df = make_df([cell() for _ in range(300)])
    assert assert_facet_matches_filter(df) >= 5


def test_invariant_and_legacy_whole_string_agree_on_single_tokens():
    """For a facet option (always a single token) the whole-string half can never
    add a row the token half missed, so it cannot break the invariant."""
    df = make_df()
    for opt in compute_facets(df)["doc_type"]:
        token_rows = {i for i, d in enumerate(CORPUS)
                      if opt["value"].upper() in {t.upper() for t in _dt_tokens(d)}}
        assert set(matched_rows(df, [opt["value"]])) == token_rows


# ── Through the HTTP API (saved preset -> export) ────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    sys.path.insert(0, str(_API_ROOT))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from main import app
    return TestClient(app)


@pytest.fixture
def project_with_doctypes(client, tmp_path):
    """A project whose active Smart analysis holds the crafted DT corpus."""
    from services import analyses, dataset_io

    pid = client.post("/api/projects", json={"name": "DT"}).json()["id"]
    analysis_id, adir = analyses.create_analysis(pid, "smart")
    dataset_io.atomic_write_dataset(make_df(), adir / "merged.xlsx")
    analyses.finalize_analysis(pid, analysis_id)
    return pid


def _filter(client, pid, spec):
    r = client.post(f"/api/projects/{pid}/filter",
                    json={"spec": spec, "limit": 1, "include_facets": True})
    assert r.status_code == 200, r.text
    return r.json()


def test_api_facet_option_total_equals_count(client, project_with_doctypes):
    pid = project_with_doctypes
    facet = _filter(client, pid, {})["facets_all"]["doc_type"]
    assert {o["value"].upper() for o in facet} == {
        "ARTICLE", "EARLY ACCESS", "PROCEEDINGS PAPER", "REVIEW", "BOOK CHAPTER"}
    for opt in facet:
        total = _filter(client, pid, {"doc_type": [opt["value"]]})["total"]
        assert total == opt["count"], opt
    assert _filter(client, pid, {"doc_type": ["Article"]})["total"] == 16


def test_api_legacy_preset_still_exports(client, project_with_doctypes, tmp_path):
    """A preset saved with a composite value must not turn into a 0-row export
    (the exporter refuses an empty result)."""
    pid = project_with_doctypes
    spec = {"doc_type": ["Article; Early Access"]}
    r = client.post(f"/api/projects/{pid}/filter/presets", json={"name": "legacy", "spec": spec})
    assert r.status_code == 200
    stored = next(p for p in client.get(f"/api/projects/{pid}/filter/presets").json()
                  if p["name"] == "legacy")["spec"]
    assert stored == spec

    r = client.post(f"/api/projects/{pid}/export",
                    json={"fmt": "csv", "filter": stored, "output_name": "legacy.csv"})
    assert r.status_code == 200, r.text
    with (tmp_path / pid / "exports" / "legacy.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 4  # rows 6-8 verbatim + row 13 (case-insensitive)


def test_api_article_export_includes_composites(client, project_with_doctypes, tmp_path):
    pid = project_with_doctypes
    r = client.post(f"/api/projects/{pid}/export",
                    json={"fmt": "csv", "filter": {"doc_type": ["Article"]}, "output_name": "art.csv"})
    assert r.status_code == 200, r.text
    with (tmp_path / pid / "exports" / "art.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 16
    assert {"Article; Early Access", "Article; Proceedings Paper", "Article; Book Chapter"} <= {
        r["DT"] for r in rows}
