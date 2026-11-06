"""Converter and MergeDB behaviour must not depend on the pandas major version.

Under pandas 3 text columns are the ``str`` dtype instead of ``object``; the
converters used to gate their upper-casing on ``dtype == 'object'`` and silently
skipped every column. MergeDB built title+year keys with ``astype(str)``, which
keeps NaN under pandas 3 and made records with a blank year disappear.
Run on pandas 2, pandas 2 + ``BIBEXPY_PANDAS3_SEMANTICS=1`` and real pandas 3.
"""

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))
_PACKAGES = _API_ROOT.parents[1] / "packages"
if str(_PACKAGES) not in sys.path:
    sys.path.insert(0, str(_PACKAGES))
    for _mod in [m for m in sys.modules if m == "bibex_core" or m.startswith("bibex_core.")]:
        sys.modules.pop(_mod, None)

from bibex_core.MergeDB import merge_db_sources  # noqa: E402
from bibex_core.scp2xlsx import csvScopus2df  # noqa: E402
from bibex_core.wos2xlsx import isi2df  # noqa: E402

_SAMPLE = Path(
    os.environ.get(
        "BIBEXPY_SAMPLE_DATA",
        _API_ROOT.parents[1] / "python_pkg" / "src" / "bibexpy" / "_samples" / "simple_project",
    )
)
SCOPUS_CSV = _SAMPLE / "scopus.csv"
WOS_TXT = _SAMPLE / "savedrecs.txt"
KEEP_CASE = {"DI", "URL"}

needs_sample = pytest.mark.skipif(
    not (SCOPUS_CSV.exists() and WOS_TXT.exists()), reason="bundled sample not available"
)


def _is_textual(s):
    return pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s)


def _assert_all_upper(df, skip=()):
    for col in df.columns:
        if col in skip or not _is_textual(df[col]):
            continue
        bad = [v for v in df[col] if isinstance(v, str) and v != v.upper()]
        assert not bad, f"{col}: not upper-case, e.g. {bad[0]!r}"


@needs_sample
def test_scopus_sample_uppercase_except_di_url():
    raw = pd.read_csv(SCOPUS_CSV, dtype=str, na_values="", keep_default_na=False).fillna("")
    out = csvScopus2df(str(SCOPUS_CSV))
    _assert_all_upper(out, skip=KEEP_CASE)
    # DI / URL keep the exact input text.
    for tag, orig in (("DI", "DOI"), ("URL", "Link")):
        assert tag in out.columns
        assert list(out[tag]) == list(raw[orig])
    # Titles really were upper-cased (the pandas 3 failure mode left them as is).
    assert out["TI"].iloc[0] == raw["Title"].iloc[0].upper()


@needs_sample
def test_wos_sample_uppercase_except_di():
    lines = WOS_TXT.read_text(encoding="utf-8").splitlines(keepends=True)
    out = isi2df(lines)
    _assert_all_upper(out, skip={"DI"})
    di_raw = [
        ln[3:].strip() for ln in lines if ln.startswith("DI ")
    ]
    di_out = [v for v in out["DI"] if isinstance(v, str)]
    assert di_out == di_raw  # unchanged vs input
    assert out["TI"].map(lambda v: isinstance(v, str)).any()


def test_scopus_sharp_s_uses_python_upper(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text(
        'Authors,Title,Source title,Year,DOI,Link,Affiliations,Abstract\n'
        '"Meißner A.","Straße der Forschung","Zeitschrift für Maßstab",2020,'
        '10.1/abc.Def,https://x.org/Ab,"Universität Groß, Germany","Große Studie"\n',
        encoding="utf-8",
    )
    out = csvScopus2df(str(p))
    assert out["AU"].iloc[0] == "MEISSNER A"
    assert out["TI"].iloc[0] == "STRASSE DER FORSCHUNG"
    assert out["SO"].iloc[0] == "ZEITSCHRIFT FÜR MASSSTAB"
    assert out["AB"].iloc[0] == "GROSSE STUDIE"
    assert "ẞ" not in "".join(str(v) for v in out.iloc[0])
    assert out["DI"].iloc[0] == "10.1/abc.Def"
    assert out["URL"].iloc[0] == "https://x.org/Ab"


def _frame(db, titles, years):
    n = len(titles)
    return pd.DataFrame(
        {
            "DB": [db] * n,
            "TI": titles,
            "PY": years,
            "AU": [f"AUTHOR {i}" for i in range(n)],
            "DI": [None] * n,
        }
    )


@pytest.mark.parametrize("merge_fields", [True, False])
def test_mergedb_blank_year_keeps_all_records(merge_fields):
    nan = float("nan")
    isi = _frame("ISI", [f"Title number {i} of ISI" for i in range(4)], [2020.0, nan, 2021.0, nan])
    sco = _frame("SCOPUS", [f"Title number {i} of SCOPUS" for i in range(3)], [nan, 2019.0, nan])
    out = merge_db_sources(isi, sco, remove_duplicated=True, merge_fields=merge_fields)
    assert len(out) == 7
    titles = set(out["TI"])
    assert "Title number 1 of ISI" in titles and "Title number 0 of SCOPUS" in titles


def test_mergedb_blank_year_same_title_still_merges():
    nan = float("nan")
    a = _frame("ISI", ["Same Title"], [nan])
    b = _frame("SCOPUS", ["same title"], [nan])
    assert len(merge_db_sources(a, b, merge_fields=True)) == 1
    a = _frame("ISI", ["Same Title"], [nan])
    b = _frame("SCOPUS", ["same title"], [nan])
    assert len(merge_db_sources(a, b, merge_fields=False)) == 1


def test_mergedb_all_empty_cr_float_column_no_warning(recwarn):
    nan = float("nan")
    a = _frame("ISI", ["A one"], [2020.0])
    b = _frame("SCOPUS", ["B two"], [2021.0])
    a["CR"] = nan
    b["CR"] = nan
    out = merge_db_sources(a, b)
    assert len(out) == 2
    assert not [w for w in recwarn if "fill value" in str(w.message)]
