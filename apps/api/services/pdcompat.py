"""Helpers that give the same result under pandas 2 and pandas 3.

BibexPy 2.4.x supports pandas 2.2+ and pandas 3 while users move to pandas 3 (see the
README, "Python and pandas support"). pandas 3 changed four behaviours this code relies on:

* text columns get the new ``str`` dtype, so a ``dtype == "object"`` test no longer finds them;
* ``astype(str)`` keeps a missing value missing instead of turning it into the text ``"nan"``;
* writing a value of another type into a typed column raises ``TypeError`` instead of
  silently turning the column into ``object``;
* ``Series.str.upper()`` on the Arrow-backed ``str`` dtype uses simple case mapping, so
  ``"ß"`` becomes ``"ẞ"`` where Python's ``str.upper`` (and pandas 2) gives ``"SS"``.

Code that depends on any of these goes through the helpers below, so a dataset gives the
same output whichever pandas version is installed.
"""

from __future__ import annotations

from typing import Any, Iterable

import numpy as np
import pandas as pd
from pandas.api import types as pdt


def is_text(s: pd.Series) -> bool:
    """True for an ``object`` column or a pandas string column (``str`` / ``string``)."""
    return pdt.is_object_dtype(s.dtype) or pdt.is_string_dtype(s.dtype)


def is_missing(value: Any) -> bool:
    """``pd.isna`` for one value that never raises (list-like values count as present)."""
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def text(s: pd.Series) -> pd.Series:
    """The cells as Python strings with missing cells as ``""`` (``object`` dtype).

    Replaces ``s.astype(str)`` where the result is compared with ``""`` / ``"NAN"`` or
    concatenated: pandas 2 turned a missing cell into ``"nan"``, pandas 3 keeps it missing,
    and a missing value poisons string concatenation and comparisons.
    """
    return pd.Series(["" if is_missing(v) else str(v) for v in s.tolist()],
                     index=s.index, dtype=object)


def upper(s: pd.Series) -> pd.Series:
    """:func:`text` upper-cased with Python's ``str.upper`` (``"ß"`` -> ``"SS"``)."""
    return pd.Series(["" if is_missing(v) else str(v).upper() for v in s.tolist()],
                     index=s.index, dtype=object)


def blank(s: pd.Series) -> pd.Series:
    """Boolean mask of empty cells: missing, only whitespace, or the legacy text ``"nan"``."""
    t = [("" if is_missing(v) else str(v)).strip() for v in s.tolist()]
    return pd.Series([v == "" or v.upper() == "NAN" for v in t], index=s.index, dtype=bool)


def widen(df: pd.DataFrame, columns: Iterable[str]) -> None:
    """Turn the listed existing columns into ``object`` dtype, IN PLACE."""
    for col in columns:
        if col in df.columns and df[col].dtype != object:
            df[col] = df[col].astype(object)


def _fits(dtype: Any, value: Any) -> bool:
    """Whether pandas stores ``value`` in a column of ``dtype`` without changing the dtype."""
    if is_missing(value):
        return pdt.is_float_dtype(dtype) or pdt.is_string_dtype(dtype)
    if isinstance(value, str):
        return pdt.is_string_dtype(dtype)
    if isinstance(value, (bool, np.bool_)):
        return pdt.is_bool_dtype(dtype)
    if isinstance(value, (int, np.integer)):
        return pdt.is_integer_dtype(dtype) or pdt.is_float_dtype(dtype)
    if isinstance(value, (float, np.floating)):
        return pdt.is_float_dtype(dtype)
    return False


def set_cell(df: pd.DataFrame, index: Any, col: str, value: Any) -> None:
    """``df.at[index, col] = value`` that also works when the column cannot hold ``value``.

    pandas 2 upcast such a column to ``object`` (with a FutureWarning); pandas 3 raises
    ``TypeError``. The column is widened to ``object`` first in exactly those cases, so both
    versions end with the same frame. A column that does not exist yet is created as
    ``object`` (``.at`` on a missing label is deprecated in pandas 3 and will raise in 4).
    """
    if col not in df.columns:
        df[col] = pd.Series([None] * len(df), index=df.index, dtype=object)
    elif df[col].dtype != object and not _fits(df[col].dtype, value):
        df[col] = df[col].astype(object)
    df.at[index, col] = value


def value_counts_sorted(s: pd.Series, top: int | None = None) -> list[tuple[Any, int]]:
    """``(value, count)`` pairs by count (descending), then value as text (ascending).

    ``Series.value_counts`` leaves the order of equal counts unspecified and it differs
    between pandas 2 and 3; facets and charts cut the list at a top-N, so the tie order
    decides what is shown.
    """
    pairs = sorted(((k, int(n)) for k, n in s.value_counts().items()),
                   key=lambda kv: (-kv[1], str(kv[0])))
    return pairs if top is None else pairs[:top]


def ordered_union(*column_lists: Iterable[Any]) -> list[Any]:
    """Column labels of several frames in first-seen order.

    Iterating a ``set`` of strings depends on the per-process hash seed, so a column order
    built from one changes from run to run.
    """
    return list(dict.fromkeys(c for cols in column_lists for c in cols))
