"""Shared pytest configuration for the backend suite.

Setting ``BIBEXPY_PANDAS3_SEMANTICS=1`` runs the whole suite under pandas 3's
string semantics WITHOUT installing pandas 3: pandas 2.2.x reproduces them
exactly through ``future.infer_string``. Under those semantics a text-returning
``Series.apply()`` yields a ``str``-dtype column in which the ``None`` that the
normalize_* functions return for "empty" becomes a **truthy** ``NaN`` — the
condition that silently destroyed every DOI-less record before the identity
guards moved to ``_present()``. CI therefore runs the suite twice: once plain,
once with this flag.
"""

import os

import pandas as pd

if os.environ.get("BIBEXPY_PANDAS3_SEMANTICS") == "1":
    pd.options.future.infer_string = True
