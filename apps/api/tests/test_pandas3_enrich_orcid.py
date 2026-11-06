"""Enrichment / ORCID back-fill give the same result under pandas 2, pandas 2 with
pandas-3 string semantics and real pandas 3 (frames born from xlsx: all-blank columns are
float64, numeric columns with blanks are float64). The network is always stubbed."""

import asyncio

import numpy as np
import pandas as pd
import pytest

from services import enricher
from services.disambiguation import orcid as orcid_mod
from services.disambiguation import pipeline


class _Ctx:
    cancelled = False

    def __init__(self):
        self.logs = []

    def log(self, msg, **kw):
        self.logs.append(msg)

    def progress(self, *_a, **_k):
        pass


def _xlsx_frame():
    return pd.DataFrame({
        "UID": ["1", "2", "3"],
        "DI": ["10.1/a", "10.1/b", "10.1/c"],
        "TI": ["T a", "T b", "T c"],
        "PY": [np.nan, 2019.0, np.nan],
        "TC": [np.nan, np.nan, 5.0],
        "AB": [np.nan, np.nan, np.nan],
        "DE": ["kw", None, None],
    })


def _stub_meta(monkeypatch, calls):
    from bibex_core.modules import api_utils

    def fake(doi, current, **_kw):
        calls.append(doi)
        return {"AB": f"An abstract {doi}", "PY": "2020", "TC": "12", "DE": "k1; k2"}

    monkeypatch.setattr(api_utils, "extract_metadata", fake)


def test_api_pass_fills_typed_columns(monkeypatch):
    calls = []
    _stub_meta(monkeypatch, calls)
    df = _xlsx_frame()
    ctx = _Ctx()
    res = asyncio.run(enricher._api_pass(ctx, df))
    assert res == {"total": 3, "enriched": 3}
    assert calls == ["10.1/a", "10.1/b", "10.1/c"]
    assert df["AB"].tolist() == ["An abstract 10.1/a", "An abstract 10.1/b", "An abstract 10.1/c"]
    assert df["PY"].tolist()[1] == 2019.0 and df["PY"].tolist()[0] == "2020"
    assert df["TC"].tolist()[0] == "12" and df["TC"].tolist()[2] == 5.0
    assert df["DE"].tolist() == ["kw", "k1; k2", "k1; k2"]
    assert not any("Invalid value" in m for m in ctx.logs)


def test_api_pass_skips_blank_dois(monkeypatch):
    calls = []
    _stub_meta(monkeypatch, calls)
    df = _xlsx_frame()
    df["DI"] = pd.Series([np.nan, "10.1/a", np.nan], dtype=object)
    res = asyncio.run(enricher._api_pass(_Ctx(), df))
    assert calls == ["10.1/a"]
    assert res["total"] == 1
    # an all-NaN float DI column: no lookups at all
    df2 = _xlsx_frame()
    df2["DI"] = np.nan
    calls.clear()
    assert asyncio.run(enricher._api_pass(_Ctx(), df2))["total"] == 0
    assert calls == []


def test_api_pass_reverts_suspect_doi_in_float_column(monkeypatch):
    from bibex_core.modules import api_utils
    monkeypatch.setattr(api_utils, "extract_metadata", lambda doi, cur, **k: {"TI": "Completely other"})
    monkeypatch.setattr(api_utils, "same_work_titles", lambda a, b: (False, 0.1, "no"))
    df = _xlsx_frame()
    verdicts = {}
    asyncio.run(enricher._api_pass(_Ctx(), df, fresh={0: "T a"}, verdicts=verdicts))
    assert verdicts == {0: "suspect"}
    assert df.at[0, "DI"] == ""


def test_mirror_wc_sc_fills_from_sc_when_wc_all_nan():
    df = pd.DataFrame({"WC": [np.nan, np.nan, np.nan], "SC": ["Physics", None, "Chem"]})
    assert enricher._mirror_wc_sc(df) == 2
    assert df["WC"].tolist()[0] == "Physics" and df["WC"].tolist()[2] == "Chem"
    assert pd.isna(df["WC"].tolist()[1])
    df = pd.DataFrame({"WC": ["A", None], "SC": [np.nan, np.nan]})
    assert enricher._mirror_wc_sc(df) == 1
    assert df["SC"].tolist()[0] == "A"


def test_mirror_wc_sc_without_work_keeps_dtype():
    df = pd.DataFrame({"WC": [np.nan, np.nan], "SC": [np.nan, np.nan]})
    assert enricher._mirror_wc_sc(df) == 0
    assert df["WC"].dtype == np.float64


def test_enrich_rows_from_doi_fills_float_columns(monkeypatch):
    rows = [("A B", "0000-0001-2345-6789", "https://ror.org/x", "US")]
    monkeypatch.setattr(orcid_mod, "fetch_orcids_for_doi", lambda d, email: rows)
    df = pd.DataFrame({"UID": ["1", "2"], "DI": ["10.1/a", np.nan],
                       "OI": [np.nan, np.nan], "RI": [np.nan, np.nan],
                       "ROR": [np.nan, np.nan], "CC": [np.nan, np.nan]})
    res = orcid_mod.enrich_rows_from_doi(df, ["1", "2"], email=None)
    assert res == {"rows_filled": 1, "dois_fetched": 1}
    assert df.at[0, "OI"] == "A B/0000-0001-2345-6789"
    assert df.at[0, "ROR"] == "https://ror.org/x" and df.at[0, "CC"] == "US"
    assert pd.isna(df.at[1, "OI"])


def test_apply_filled_cells_into_float_columns():
    full = pd.DataFrame({"UID": ["1", "2"], "OI": [np.nan, np.nan], "RI": [np.nan, np.nan]})
    part = pd.DataFrame({"UID": ["2"], "OI": ["A B/0000-0001-2345-6789"], "RI": [None]})
    out = pipeline._apply_filled_cells(full, part)
    assert out["OI"].tolist()[1] == "A B/0000-0001-2345-6789"
    assert pd.isna(out["OI"].tolist()[0])
    assert full["OI"].isna().all()   # the input is untouched


def test_c1_map_keeps_missing_cells_as_pandas2_did():
    df = pd.DataFrame({"C1": ["Univ X, Turkey", None]})
    n = pipeline._apply_c1_map(df, ["C1"], lambda s: s.replace("Turkey", "TR"))
    assert n == 1
    assert df["C1"].tolist() == ["Univ X, TR", "nan"]
