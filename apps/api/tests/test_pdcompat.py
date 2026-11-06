"""services.pdcompat gives the same results under pandas 2, pandas 2 with pandas-3 string
semantics, and pandas 3 (CI runs this file in all three legs)."""

import numpy as np
import pandas as pd
import pytest

from services import pdcompat


def test_is_text_finds_object_and_str_columns():
    assert pdcompat.is_text(pd.Series(["a", None], dtype=object))
    assert pdcompat.is_text(pd.Series(["a", None], dtype="string"))
    assert pdcompat.is_text(pd.Series(["a", "b"]))  # object in pandas 2, str in pandas 3
    assert not pdcompat.is_text(pd.Series([1.0, np.nan]))
    assert not pdcompat.is_text(pd.Series([1, 2]))


def test_text_turns_missing_into_empty_string():
    s = pd.Series(["Title", None, np.nan, 2020.0, 7])
    out = pdcompat.text(s)
    assert out.dtype == object
    assert out.tolist() == ["Title", "", "", "2020.0", "7"]
    # A whole-NaN float column (what read_excel gives for an empty column).
    assert pdcompat.text(pd.Series([np.nan, np.nan])).tolist() == ["", ""]


def test_upper_uses_python_case_mapping():
    s = pd.Series(["Straße", "Meißner, M.", None, "ﬁsh", "İstanbul"])
    assert pdcompat.upper(s).tolist() == ["STRASSE", "MEISSNER, M.", "", "FISH", "İSTANBUL"]
    # Same result when the input already has the pandas string dtype.
    assert pdcompat.upper(s.astype("string")).tolist() == ["STRASSE", "MEISSNER, M.", "", "FISH", "İSTANBUL"]


def test_blank_mask():
    s = pd.Series(["x", "", "  ", None, np.nan, "nan", "NaN", "None", "0"])
    assert pdcompat.blank(s).tolist() == [False, True, True, True, True, True, True, False, False]
    assert pdcompat.blank(pd.Series([2020.0, np.nan])).tolist() == [False, True]


@pytest.mark.parametrize(
    "values, value, expect_dtype_object",
    [
        ([2001, 2002], "1999", True),        # text into int64 (the record editor sends text)
        ([2001, 2002], 1999, False),         # int into int64 stays int64
        ([1.0, np.nan], 3, False),           # int into float64 stays float64
        ([1.0, np.nan], "abc", True),        # text into float64
        ([np.nan, np.nan], "An abstract", True),  # text into an empty column read from xlsx
        ([1, 2], None, True),                # blank into int64
        ([1.0, 2.0], None, False),           # blank into float64 -> NaN
    ],
)
def test_set_cell_matches_pandas2_upcast(values, value, expect_dtype_object):
    df = pd.DataFrame({"c": values, "other": ["a", "b"]})
    pdcompat.set_cell(df, 0, "c", value)
    got = df.at[0, "c"]
    if value is None:
        assert pdcompat.is_missing(got)
    else:
        assert got == value
    assert df.at[1, "c"] == values[1] or (pdcompat.is_missing(values[1]) and pdcompat.is_missing(df.at[1, "c"]))
    assert (df["c"].dtype == object) is expect_dtype_object


def test_set_cell_into_text_column_and_new_column():
    df = pd.DataFrame({"TI": ["a", "b"]})
    pdcompat.set_cell(df, 1, "TI", "c")
    assert df["TI"].tolist() == ["a", "c"]
    pdcompat.set_cell(df, 0, "NEW", "x")
    assert df.at[0, "NEW"] == "x"


def test_widen():
    df = pd.DataFrame({"PY": [2001, 2002], "TI": ["a", "b"]})
    pdcompat.widen(df, ["PY", "MISSING"])
    assert df["PY"].dtype == object
    assert df["PY"].tolist() == [2001, 2002]


def test_value_counts_sorted_breaks_ties_by_value():
    s = pd.Series(["b", "a", "d", "d", "c", "c", None])
    assert pdcompat.value_counts_sorted(s) == [("c", 2), ("d", 2), ("a", 1), ("b", 1)]
    assert pdcompat.value_counts_sorted(s, top=3) == [("c", 2), ("d", 2), ("a", 1)]


def test_ordered_union_keeps_first_seen_order():
    assert pdcompat.ordered_union(["TI", "PY", "UID"], ["PY", "SO", "TI", "DB"]) == ["TI", "PY", "UID", "SO", "DB"]
