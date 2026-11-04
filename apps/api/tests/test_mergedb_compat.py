"""v1-compat merge path (``bibex_core.MergeDB.merge_db_sources``) regressions.

The server pipeline uses ``services.smart_merger``, but the packaged
``bibex_core`` module is still what headless users import from a notebook or a
script, so the defects fixed here only ever showed up outside the web UI:

* the title key was built without lowercasing, so ``Deep Learning`` and
  ``Deep learning`` produced two keys and the same article survived twice;
* dedup grouped on the RAW ``DI`` value, so case, ``https://doi.org/`` prefixes
  and ``_`` vs ``-`` separator spellings each split one article into two groups;
* Scopus-grammar ``CR`` was written out unconverted, so those records silently
  dropped out of every coupling network downstream (84 of 436 records in the
  reported corpus).

``merge_db_sources`` itself performs no file I/O — only ``MergeDB.main()``
writes xlsx — so these tests call it directly on in-memory frames.
"""

import re
import sys
from pathlib import Path

import pandas as pd

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

# bibex_core lives in <repo>/packages and is put on the path by the dev launcher
# (PYTHONPATH=<repo>\packages).  Mirror that here so the tests exercise the repo
# copy rather than any stale wheel installed into site-packages.
_PACKAGES = _API_ROOT.parents[1] / "packages"
if str(_PACKAGES) not in sys.path:
    sys.path.insert(0, str(_PACKAGES))
    for _mod in [m for m in sys.modules if m == "bibex_core" or m.startswith("bibex_core.")]:
        sys.modules.pop(_mod, None)

from bibex_core.MergeDB import merge_db_sources  # noqa: E402
from bibex_core.cr_normalize import count_refs  # noqa: E402


# Real Scopus "new format" CR — authors separated by ';', reference ends with "(yyyy)".
SCOPUS_CR = (
    "FAHLE L.; HOLLEY E.A.; WALTON G., ANALYSIS OF SLAM-BASED LIDAR DATA, "
    "MIN METALL EXPLOR, 39, 5, PP. 1939-1960, (2022); "
    "PIVAC D., AVAILABILITY OF HISTORICAL CADASTRAL DATA, LAND, 10, 9, (2021); "
    "ALSTON B.; HARRELL J.; SHAW I., ANCIENT EGYPTIAN MATERIALS, (2000)"
)
WOS_CR = (
    "MILDENHALL B, 2022, COMMUN ACM, V65, P99, DOI 10.1145/3503250;"
    "PASZKE A, 2019, ADV NEUR IN, V32"
)


def _wos(**overrides) -> pd.DataFrame:
    row = {
        "DB": "ISI",
        "AU": "SMITH J",
        "AF": "SMITH, JOHN",
        "TI": "Deep Learning In Bibliometrics",
        "PY": 2020,
        "SO": "SCIENTOMETRICS",
        "RP": "",
        "CR": WOS_CR,
        "NR": 2,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _scopus(**overrides) -> pd.DataFrame:
    row = {
        "DB": "SCOPUS",
        "AU": "Smith, John",
        "AF": "SMITH, JOHN (58490132900)",
        "TI": "Deep learning in bibliometrics",
        "PY": 2020,
        "SO": "Scientometrics",
        "RP": "",
        "CR": SCOPUS_CR,
        "NR": "",
    }
    row.update(overrides)
    return pd.DataFrame([row])


# ── (a) Title key is lowercased ───────────────────────────────────────────

def test_case_differing_title_year_pair_dedupes():
    """'Deep Learning In Bibliometrics' and 'Deep learning in bibliometrics'
    are the same record; without lower() two separate keys were produced and the
    duplicate was left in the output. There is no DOI column — only the
    title+year path is exercised."""
    merged = merge_db_sources(_wos(), _scopus())
    assert len(merged) == 1, merged[["TI", "PY"]].to_dict("records")


def test_different_titles_are_not_over_merged():
    """Control: lower() must not merge genuinely different records."""
    merged = merge_db_sources(
        _wos(TI="Deep Learning In Bibliometrics"),
        _scopus(TI="Machine learning for citation networks"),
    )
    assert len(merged) == 2


def test_same_title_different_year_kept_separate():
    merged = merge_db_sources(_wos(PY=2020), _scopus(PY=2015))
    assert len(merged) == 2


# ── (b) DOI separator/prefix/case variants collapse into a single group ───

def test_separator_variant_doi_pair_dedupes():
    """REAL CASE: WoS gives '10.4103/jgid.jgid_12_19', Scopus gives the same
    article as '10.4103/jgid.jgid-12-19'. The titles are deliberately DIFFERENT
    here — the only merging path is the DOI key."""
    merged = merge_db_sources(
        _wos(TI="Global infection trends", DI="10.4103/jgid.jgid_12_19"),
        _scopus(TI="A completely unrelated heading here",
                DI="https://doi.org/10.4103/JGID.JGID-12-19"),
    )
    assert len(merged) == 1, merged[["TI", "DI"]].to_dict("records")


def test_distinct_dois_stay_separate():
    """Control: genuinely different DOIs (and different titles) stay separate."""
    merged = merge_db_sources(
        _wos(TI="Alpha study", DI="10.4103/jgid.jgid_12_19"),
        _scopus(TI="Beta study", DI="10.4103/jgid.jgid_13_19"),
    )
    assert len(merged) == 2


def test_records_without_doi_still_reach_title_path():
    """Rows whose DOI is empty/invalid (key None) must fall back to the
    title+year path as before — they must not get lost in the DOI group."""
    merged = merge_db_sources(_wos(DI=""), _scopus(DI="n/a"))
    assert len(merged) == 1


# ── (c) CR is converted from Scopus grammar to WoS grammar, NR is counted ─

def test_scopus_cr_is_rewritten_and_nr_recounted():
    """Single-source (Scopus-only) merge: CR is fully converted to WoS grammar
    and NR is filled in from the reference count.

    With a single source ``len(M['DB'].unique()) > 1`` is false, so the
    field-merging block (and therefore ``merge_references``) never runs; this
    test thus measures only the CR normalization step.
    """
    merged = merge_db_sources(_scopus(TI="Beta study", DI="10.1/beta"))
    assert len(merged) == 1

    row = merged.iloc[0]
    assert row["CR"] == (
        "FAHLE L, 2022, MIN METALL EXPLOR, V39, P1939; "
        "PIVAC D, 2021, LAND, V10; "
        "ALSTON B, 2000"
    )
    assert re.search(r"\(\d{4}\)\s*;", str(row["CR"])) is None
    assert re.search(r"\(\d{4}\)\s*$", str(row["CR"])) is None
    # NR was empty → must be counted from CR
    assert int(row["NR"]) == count_refs(row["CR"]) == 3


def test_wos_cr_passes_through_untouched():
    merged = merge_db_sources(_wos(TI="Alpha study", DI="10.1/alpha"))
    row = merged.iloc[0]
    assert row["CR"] == WOS_CR
    assert int(row["NR"]) == 2


def test_mixed_source_output_carries_no_scopus_cr_grammar():
    """Mixed WoS+Scopus merge: no Scopus grammar residue ('(yyyy);' or a trailing
    '(yyyy)' at the end of the line) may remain anywhere in the output, and NR must
    equal the number of references in CR.

    NOTE: in this branch MergeDB's own ``merge_references`` step splits the Scopus
    CR on ';' (in Scopus ';' is the AUTHOR separator), fragmenting the references
    and reordering them. This is a separate, PRE-EXISTING defect; what is verified
    here is not the order/matching of the CR contents, only grammar and NR
    consistency."""
    merged = merge_db_sources(
        _wos(TI="Alpha study", DI="10.1/alpha"),
        _scopus(TI="Beta study", DI="10.1/beta"),
    )
    assert len(merged) == 2

    cr_all = "; ".join(str(v) for v in merged["CR"])
    assert re.search(r"\(\d{4}\)\s*;", cr_all) is None, cr_all
    assert re.search(r"\(\d{4}\)\s*$", cr_all) is None, cr_all

    for _, row in merged.iterrows():
        assert int(row["NR"]) == count_refs(row["CR"])


def test_zero_nr_with_non_empty_cr_is_recounted():
    """NR=0 + non-empty CR is the wrong value left by old merges; it is recounted from CR."""
    merged = merge_db_sources(
        _wos(TI="Alpha study", DI="10.1/alpha", NR=0),
        _scopus(TI="Beta study", DI="10.1/beta", NR=0),
    )
    for _, row in merged.iterrows():
        assert int(row["NR"]) == count_refs(row["CR"])
        assert int(row["NR"]) > 0


def test_empty_cr_keeps_nr_untouched():
    """On a genuinely reference-less record (empty CR) NR is left untouched."""
    merged = merge_db_sources(
        _wos(TI="Alpha study", DI="10.1/alpha", CR="", NR=0),
        _scopus(TI="Beta study", DI="10.1/beta", CR="", NR=""),
    )
    alpha = merged[merged["TI"].astype(str).str.contains("Alpha")].iloc[0]
    assert str(alpha["NR"]).strip() in ("0", "0.0")


def test_nr_column_created_when_absent():
    """A Scopus export has no NR column; if CR is present, NR must be generated."""
    w = _wos(TI="Alpha study", DI="10.1/alpha").drop(columns=["NR"])
    s = _scopus(TI="Beta study", DI="10.1/beta").drop(columns=["NR"])
    merged = merge_db_sources(w, s)
    assert "NR" in merged.columns
    for _, row in merged.iterrows():
        assert int(row["NR"]) == count_refs(row["CR"])


# ── (d) Helper/temporary columns must not appear in the output ────────────

_TEMP_COLS = {"_doi_key", "clean_title", "title_year", "RP_WOS", "RP_SCOPUS"}


def test_helper_columns_absent_from_output():
    merged = merge_db_sources(
        _wos(TI="Alpha study", DI="10.4103/jgid.jgid_12_19"),
        _scopus(TI="Beta study", DI="https://doi.org/10.4103/JGID.JGID-13-19"),
    )
    assert _TEMP_COLS.isdisjoint(merged.columns), sorted(set(merged.columns) & _TEMP_COLS)


def test_helper_columns_absent_without_merge_fields():
    """Helper columns must not leak in the merge_fields=False branch either."""
    merged = merge_db_sources(
        _wos(TI="Alpha study", DI="10.4103/jgid.jgid_12_19"),
        _scopus(TI="Beta study", DI="https://doi.org/10.4103/JGID.JGID-12-19"),
        merge_fields=False,
    )
    assert len(merged) == 1
    assert _TEMP_COLS.isdisjoint(merged.columns), sorted(set(merged.columns) & _TEMP_COLS)


def test_no_dedup_path_still_normalizes_cr():
    """The CR/NR fix is applied even when remove_duplicated=False."""
    merged = merge_db_sources(_wos(), _scopus(), remove_duplicated=False)
    assert len(merged) == 2
    assert re.search(r"\(\d{4}\)\s*;", "; ".join(str(v) for v in merged["CR"])) is None
    assert _TEMP_COLS.isdisjoint(merged.columns)


def test_merge_references_does_not_shred_raw_scopus_cr():
    """Defect #5: merge_references used to split raw Scopus CR on a bare ';' and
    shred it into author fragments (breaking the author↔reference mapping). It now
    normalizes on input — no fragments in the output, 2 references stay 2."""
    from bibex_core.MergeDB import merge_references

    scopus_cr = ("FAHLE L.; HOLLEY E.A.; WALTON G., ANALYSIS OF SLAM DATA, "
                 "MIN METALL EXPLOR, 39, 5, PP. 1939-1960, (2022); "
                 "PIVAC D.; MARTIN R., ANOTHER TITLE, AUTOM CONSTR, 119, (2021)")
    out = merge_references("", scopus_cr)
    refs = [r.strip() for r in out.split(";") if r.strip()]
    assert len(refs) == 2, refs
    assert any(r.startswith("FAHLE L, 2022") for r in refs), refs
    assert any(r.startswith("PIVAC D, 2021") for r in refs), refs
    # An author fragment must not stand alone as a reference
    assert not any(r in ("HOLLEY E.A.", "WALTON G.", "MARTIN R.") for r in refs)

    # Combination with a WoS input: the WoS reference is preserved verbatim
    wos_cr = "SMITH J, 2020, J DOC, V66, P214"
    both = merge_references(wos_cr, scopus_cr)
    assert "SMITH J, 2020, J DOC, V66, P214" in both
    assert len([r for r in both.split(";") if r.strip()]) == 3
