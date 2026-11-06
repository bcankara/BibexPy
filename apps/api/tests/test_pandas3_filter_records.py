"""Filter, quality and record-edit behaviour that must be identical under pandas 2 and pandas 3.

Real TestClient requests on a small parquet-backed dataset whose dtypes are the ones
``read_dataset`` hands the app: float PY/TC with a blank cell, int NR, an all-empty float DE.
"""

import sys

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from main import app
    return TestClient(app)


@pytest.fixture
def project(client):
    from services import analyses, dataset_io as dio

    pid = client.post("/api/projects", json={"name": "P3FilterRecords"}).json()["id"]
    analysis_id, adir = analyses.create_analysis(pid, "smart")
    df = pd.DataFrame({
        "UID": list(range(1, 7)),
        "TI": ["Learning to learn", "Graph theory", "Deep nets", "Learning at scale",
               "Optics", "Plasma physics"],
        "AB": ["", "About learning", "", "", "Light", ""],
        "DE": pd.Series([np.nan] * 6, dtype="float64"),       # blank in the whole dataset
        "ID": ["", "", "", "", "", ""],
        "AU": ["Weiß, A", "Straße, B", "Smith, J", "Smith, J", "Lee, K", "Lee, K"],
        "SO": ["Jb", "Ja", "Jd", "Jd", "Jc", "Jc"],
        "PY": [2020.0, np.nan, 2022.0, 2021.0, 2020.0, 2021.0],   # one blank year
        "TC": [1.0, np.nan, 3.0, 4.0, 5.0, 6.0],
        "NR": [10, 11, 12, 13, 14, 15],
        "DB": ["WOS"] * 6,
    })
    dio.atomic_write_dataset(df, adir / "merged.parquet")
    analyses.finalize_analysis(pid, analysis_id)
    return pid


def _uids(client, pid, spec):
    r = client.post(f"/api/projects/{pid}/filter", json={"spec": spec, "limit": 100})
    assert r.status_code == 200, r.text
    return sorted(int(rec["UID"]) for rec in r.json()["records"])


def test_quality_missing_and_has_treat_a_blank_number_as_blank(client, project):
    assert _uids(client, project, {"quality": {"missing": ["PY"]}}) == [2]
    assert _uids(client, project, {"quality": {"has": ["PY"]}}) == [1, 3, 4, 5, 6]
    assert _uids(client, project, {"quality": {"missing": ["TC"]}}) == [2]
    assert _uids(client, project, {"quality": {"missing": ["DE"]}}) == [1, 2, 3, 4, 5, 6]
    assert _uids(client, project, {"quality": {"has": ["DE"]}}) == []


def test_fulltext_survives_an_all_empty_float_column(client, project):
    spec = {"fulltext": {"query": "learning", "fields": ["TI", "AB", "DE", "ID"]}}
    assert _uids(client, project, spec) == [1, 2, 4]
    spec = {"fulltext": {"query": "learning NOT scale", "fields": ["TI", "AB", "DE", "ID"]}}
    assert _uids(client, project, spec) == [1, 2]
    spec = {"fulltext": {"query": "optics OR plasma", "fields": ["DE", "TI"]}}
    assert _uids(client, project, spec) == [5, 6]


def test_author_filter_matches_sharp_s_like_python_upper(client, project):
    assert _uids(client, project, {"authors": ["STRASSE"]}) == [2]
    assert _uids(client, project, {"authors": ["straße"]}) == [2]
    assert _uids(client, project, {"authors": ["weiß"]}) == [1]


def test_journal_facet_and_chart_break_ties_by_name(client, project):
    r = client.post(f"/api/projects/{project}/filter",
                    json={"spec": {}, "limit": 1, "include_facets": True})
    assert r.status_code == 200, r.text
    top = r.json()["facets_all"]["journal_top"]
    assert [(f["value"], f["count"]) for f in top] == [("Jc", 2), ("Jd", 2), ("Ja", 1), ("Jb", 1)]
    charts = client.get(f"/api/projects/{project}/quality/charts").json()
    assert [(j["name"], j["count"]) for j in charts["top_journals"]] == [
        ("Jc", 2), ("Jd", 2), ("Ja", 1), ("Jb", 1)]


def test_quality_stats_count_a_blank_year_as_missing(client, project):
    stats = client.get(f"/api/projects/{project}/quality/stats").json()
    by = {f["field"]: f for f in stats["fields"]}
    assert by["PY"]["filled"] == 5 and by["PY"]["missing"] == 1
    assert by["DE"]["filled"] == 0
    assert stats["db_distribution"] == {"WOS": 6}


def test_record_edit_of_numeric_columns_with_ui_strings(client, project):
    from services import filter_engine

    r = client.post(f"/api/projects/{project}/records/update",
                    json={"uid": "3", "fields": {"PY": "2030", "TC": "", "NR": "11"}})
    assert r.status_code == 200, r.text
    filter_engine._DF_CACHE.clear()                      # force a reload from the file
    df = filter_engine.load_merged(project)
    row = df[df["UID"] == 3].iloc[0]
    assert float(row["PY"]) == 2030
    assert pd.isna(row["TC"])
    assert float(row["NR"]) == 11
    # The columns come back numeric, so the numeric filters keep working on the edited rows.
    assert pd.api.types.is_numeric_dtype(df["PY"]) and pd.api.types.is_numeric_dtype(df["NR"])
    assert _uids(client, project, {"year": {"min": 2030}}) == [3]
    assert _uids(client, project, {"quality": {"missing": ["TC"]}}) == [2, 3]


def test_record_edit_of_text_and_new_columns(client, project):
    from services import filter_engine

    r = client.post(f"/api/projects/{project}/records/update",
                    json={"uid": "1", "fields": {"TI": "Edited", "NOTE": "hello", "SCORE": 5}})
    assert r.status_code == 200, r.text
    df = filter_engine.load_merged(project)
    row = df[df["UID"] == 1].iloc[0]
    assert row["TI"] == "Edited" and row["NOTE"] == "hello" and int(row["SCORE"]) == 5


def test_record_edit_with_an_impossible_value_is_a_4xx_and_changes_nothing(client, project):
    from services import filter_engine

    # "nan", "inf" and "1_0" pass float() but cannot be stored as numbers: the column would
    # silently turn into text on save.
    for fields in ({"PY": "not a year"}, {"TC": "12x"}, {"TI": ["a", "b"]},
                   {"TC": "nan"}, {"TC": "inf"}, {"PY": "2_024"}):
        r = client.post(f"/api/projects/{project}/records/update", json={"uid": "3", "fields": fields})
        assert 400 <= r.status_code < 500, (fields, r.status_code, r.text)
    df = filter_engine.load_merged(project)
    row = df[df["UID"] == 3].iloc[0]
    assert float(row["PY"]) == 2022 and float(row["TC"]) == 3
