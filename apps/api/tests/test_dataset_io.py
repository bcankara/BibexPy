"""Tests for the Parquet working format (services.dataset_io).

The active dataset and its snapshots are stored as Parquet; Excel survives only
behind explicit export paths. These tests cover the round-trip, the mixed-dtype
coercion fallback, the one-time lazy migration of legacy merged.xlsx datasets,
restoring an xlsx-era snapshot onto a Parquet dataset, and the failure modes
that must degrade quietly (unreadable dataset -> 409, unreadable legacy file ->
no migration, no exception).
"""

import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("BIBEXPY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from main import app
    return TestClient(app)


# ── T1: parquet roundtrip ────────────────────────────────────────────────

def test_parquet_roundtrip(tmp_path):
    from services import dataset_io

    p = tmp_path / "merged.parquet"
    df = pd.DataFrame({"TI": ["a", "b"], "PY": [2020, 2021]})
    dataset_io.atomic_write_dataset(df, p)
    assert p.exists()

    back = dataset_io.read_dataset(p)
    assert len(back) == 2
    assert list(back["TI"]) == ["a", "b"]
    assert list(back["PY"]) == [2020, 2021]
    assert str(back["PY"].dtype).startswith("int")  # dtype is preserved (same as in xlsx)
    # No leftover temp files
    assert not list(tmp_path.glob("*.tmp~"))


# ── T2: mixed-type coercion, input df NOT MUTATED ────────────────────────

def test_mixed_dtype_coercion_does_not_mutate_input(tmp_path):
    """Arrow rejects a mixed-type object column → first try NUMERIC recovery
    (the counterpart of the re-inference read_excel used to do on every read),
    and if it cannot be numeric, clean stringification.

    The input frame may be the frame held in the filter_engine cache; corrupting
    it would have poisoned all subsequent reads.
    """
    from services import dataset_io

    df = pd.DataFrame({"PY": [2020, "2021", None]})
    coerced = dataset_io._coerce_for_parquet(df)
    # A mixed column that can be converted to numeric stays NUMERIC — it does not fall back to string
    assert str(coerced["PY"].dtype).startswith("float")
    assert list(coerced["PY"].dropna()) == [2020.0, 2021.0]
    # The input df must be untouched
    assert df["PY"].iloc[0] == 2020 and df["PY"].iloc[2] is None

    # The write path must also accept the same frame (calling to_parquet directly raises ArrowInvalid)
    p = tmp_path / "merged.parquet"
    dataset_io.atomic_write_dataset(df, p)
    back = dataset_io.read_dataset(p)
    assert str(back["PY"].dtype).startswith("float")
    assert df["PY"].iloc[0] == 2020  # no mutation after the write either
    assert not list(tmp_path.glob("*.tmp~"))


def test_coercion_no_float_repr_artifacts(tmp_path):
    """When a mixed column that has float members and CANNOT be converted to
    numeric is stringified, it must not leave a "2020.0" artifact; and coercion
    must not spill over onto columns Arrow ALREADY accepts (pure numeric object)."""
    from services import dataset_io

    df = pd.DataFrame({
        "BAD": pd.Series([1, "x", None], dtype="object"),      # column that makes the write fail
        "MIXF": pd.Series([2020.0, "abc", None], dtype="object"),  # float + str, cannot be numeric
        "TCNUM": pd.Series([1, 2.5, 3], dtype="object"),       # pure numeric object (Arrow accepts it)
    })
    p = tmp_path / "merged.parquet"
    dataset_io.atomic_write_dataset(df, p)
    back = dataset_io.read_dataset(p)

    assert list(back["BAD"]) == ["1", "x", ""]
    # float 2020.0 → "2020", NOT "2020.0" (in the xlsx era it appeared as a number)
    assert list(back["MIXF"].dropna().astype(str))[:1] == ["2020"]
    assert "2020.0" not in back["MIXF"].astype(str).tolist()
    # No collateral damage: a numeric column does not become a string just because another column failed
    assert str(back["TCNUM"].dtype).startswith("float")
    assert list(back["TCNUM"]) == [1.0, 2.5, 3.0]


def test_write_survives_bigint_and_duplicate_columns(tmp_path):
    """Two edge cases that to_excel tolerated must not make the write fail: Python
    ints above 2^63 (OverflowError) and duplicate column labels (pyarrow
    ValueError; inside the old coercion it turned into an AttributeError)."""
    from services import dataset_io

    p1 = tmp_path / "big.parquet"
    dataset_io.atomic_write_dataset(
        pd.DataFrame({"HUGE": pd.Series([2**70, 2**71], dtype="object")}), p1)
    assert len(dataset_io.read_dataset(p1)) == 2

    p2 = tmp_path / "dup.parquet"
    dup = pd.DataFrame([[1, "a"], [2, "b"]])
    dup.columns = ["X", "X"]
    dataset_io.atomic_write_dataset(dup, p2)
    back = dataset_io.read_dataset(p2)
    assert list(back.columns) == ["X", "X.1"]  # same as read_excel's mangling
    assert not list(tmp_path.glob("*.tmp~"))


def test_coercion_leaves_numeric_columns_alone(tmp_path):
    """Numeric columns are NEVER converted to string (PY/TC consumers are numeric)."""
    from services import dataset_io

    df = pd.DataFrame({"PY": [2020, 2021], "MIX": [1, "x"]})
    coerced = dataset_io._coerce_for_parquet(df)
    assert list(coerced["PY"]) == [2020, 2021]
    assert str(coerced["PY"].dtype).startswith("int")
    assert list(coerced["MIX"]) == ["1", "x"]


# ── T3: corrupted parquet → 409 (not 500) ────────────────────────────────

def test_corrupted_parquet_returns_409_not_500(client):
    """Half-written/corrupted merged.parquet → a clean 409 instead of 500 (with a snapshot hint)."""
    from services import analyses
    from services.filter_engine import _DF_CACHE

    pid = client.post("/api/projects", json={"name": "CorruptParquet"}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "smart")
    (adir / "merged.parquet").write_bytes(b"\x00\x01half-written garbage")
    analyses.finalize_analysis(pid, aid)
    _DF_CACHE.clear()

    r = client.post(f"/api/projects/{pid}/filter", json={"spec": {}, "limit": 1})
    assert r.status_code == 409, r.text
    assert "unreadable" in r.json()["detail"]


# ── T4: lazy migration (with a real API call) ────────────────────────────

def test_lazy_migration_on_first_api_call(client):
    """Legacy merged.xlsx: on the first request a parquet is created, the xlsx
    leaves the analysis folder and is moved under snapshots/ as
    pre_parquet_migration_*.xlsx."""
    from services import analyses
    from services.filter_engine import _DF_CACHE

    pid = client.post("/api/projects", json={"name": "Legacy"}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "smart")
    pd.DataFrame({"TI": ["a", "b"], "PY": [2020, 2021]}).to_excel(
        adir / "merged.xlsx", index=False)
    analyses.finalize_analysis(pid, aid)
    _DF_CACHE.clear()

    r = client.post(f"/api/projects/{pid}/filter", json={"spec": {}, "limit": 5})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2

    assert (adir / "merged.parquet").exists()
    assert not (adir / "merged.xlsx").exists()
    snaps = list((adir / "snapshots").glob("pre_parquet_migration_*.xlsx"))
    assert len(snaps) == 1, [p.name for p in (adir / "snapshots").iterdir()]
    # The moved file holds the original xlsx bytes — it must still be readable
    assert len(pd.read_excel(snaps[0])) == 2
    assert not list(adir.glob("*.tmp~"))


# ── T5: restoring an xlsx-era snapshot onto a parquet dataset ────────────

def test_legacy_xlsx_snapshot_restores_onto_parquet_dataset(client):
    """Both restore paths (the records endpoint + the pipeline) convert the format;
    a raw byte copy would corrupt merged.parquet."""
    from services import analyses, dataset_io, storage
    from services.filter_engine import _DF_CACHE

    pid = client.post("/api/projects", json={"name": "Restore"}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "smart")
    target = adir / "merged.parquet"
    dataset_io.atomic_write_dataset(pd.DataFrame({"TI": ["x"], "PY": [2020]}), target)
    analyses.finalize_analysis(pid, aid)

    snaps = adir / "snapshots"
    snaps.mkdir(exist_ok=True)
    legacy = snaps / "pre_delete_20200101_000000.xlsx"
    pd.DataFrame({"TI": ["a", "b", "c"], "PY": [2001, 2002, 2003]}).to_excel(
        legacy, index=False)
    rel = str(legacy.relative_to(storage.settings.storage_path))
    _DF_CACHE.clear()

    # The legacy snapshot must show up in the list (xlsx + parquet together)
    listed = client.get(f"/api/projects/{pid}/records/snapshots").json()
    assert legacy.name in [it["name"] for it in listed]

    # 1) the records endpoint
    r = client.post(f"/api/projects/{pid}/records/restore-snapshot", json={"snapshot": rel})
    assert r.status_code == 200, r.text
    assert r.json()["restored"] == 3
    assert target.exists() and len(dataset_io.read_dataset(target)) == 3

    # 2) the disambiguation pipeline path
    from services.disambiguation import pipeline

    dataset_io.atomic_write_dataset(pd.DataFrame({"TI": ["y"], "PY": [2020]}), target)
    _DF_CACHE.clear()
    out = pipeline.restore_snapshot(pid, rel)
    assert out["restored_from"] == rel
    back = dataset_io.read_dataset(target)  # must be readable as a valid parquet
    assert len(back) == 3 and list(back["TI"]) == ["a", "b", "c"]


# ── T6: None/NaN normalization regression ────────────────────────────────

def test_missing_values_read_back_as_empty_string(tmp_path):
    """Parquet returns NA as None; blank checks such as `astype(str).ne("NAN")`
    cannot catch "None" → records without a DOI would be processed as if they
    had a DOI. read_dataset guarantees "" in both formats."""
    from services import dataset_io

    df = pd.DataFrame({"DI": ["10.1/a", None, ""], "AB": [None, None, "text"]})
    p = tmp_path / "merged.parquet"
    dataset_io.atomic_write_dataset(df, p)

    back = dataset_io.read_dataset(p)
    assert list(back["DI"]) == ["10.1/a", "", ""]
    assert list(back["AB"]) == ["", "", "text"]
    assert "None" not in back["DI"].astype(str).tolist()
    # The production blank test (enricher._api_pass) must count correctly: 1 record with a DOI
    with_doi = back[back["DI"].astype(str).str.strip().ne("")
                    & back["DI"].astype(str).str.upper().ne("NAN")]
    assert len(with_doi) == 1


# ── T7: read-then-immediately-replace (Windows file lock) ────────────────

def test_read_then_immediately_replace(tmp_path):
    """Reading must not keep the file open (no memory_map / ParquetFile handle);
    otherwise os.replace would raise PermissionError on Windows."""
    from services import dataset_io

    p = tmp_path / "merged.parquet"
    dataset_io.atomic_write_dataset(pd.DataFrame({"TI": list("abcdef")}), p)
    for _ in range(5):
        df = dataset_io.read_dataset(p)
        dataset_io.atomic_write_dataset(df.iloc[:-1].reset_index(drop=True), p)
    assert len(dataset_io.read_dataset(p)) == 1
    assert not list(tmp_path.glob("*.tmp~"))


# ── T8: ensure_parquet idempotent ────────────────────────────────────────

def test_ensure_parquet_is_idempotent(tmp_path):
    from services import dataset_io

    src = tmp_path / "merged.xlsx"
    pd.DataFrame({"TI": ["a"], "PY": [2020]}).to_excel(src, index=False)

    first = dataset_io.ensure_parquet(src)
    assert first == tmp_path / "merged.parquet" and first.exists()
    assert not src.exists()
    migrations = list((tmp_path / "snapshots").glob("pre_parquet_migration_*.xlsx"))
    assert len(migrations) == 1

    # Parquet path → returned as-is, NO second migration snapshot
    assert dataset_io.ensure_parquet(first) == first
    # Even if the old xlsx path is asked about again, the target already exists → it returns that
    assert dataset_io.ensure_parquet(src) == first
    assert len(list((tmp_path / "snapshots").glob("pre_parquet_migration_*.xlsx"))) == 1
    assert not list(tmp_path.glob("*.tmp~"))


# ── T9: migration does NOT raise on a corrupted legacy xlsx ──────────────

def test_ensure_parquet_on_unreadable_xlsx_returns_original(tmp_path):
    """On a corrupted legacy file the migration quietly gives up — the 409 contract
    (filter_engine) must not turn into a 500."""
    from services import dataset_io

    src = tmp_path / "merged.xlsx"
    src.write_bytes(b"\x00\x01half-written garbage")

    out = dataset_io.ensure_parquet(src)
    assert out == src and src.exists()
    assert not (tmp_path / "merged.parquet").exists()
    assert not list(tmp_path.glob("*.tmp~"))


# ── T10: empty DataFrame roundtrip ───────────────────────────────────────

def test_empty_dataframe_roundtrip(tmp_path):
    from services import dataset_io

    p = tmp_path / "merged.parquet"
    df = pd.DataFrame({"TI": pd.Series(dtype="object"), "PY": pd.Series(dtype="int64")})
    dataset_io.atomic_write_dataset(df, p)

    back = dataset_io.read_dataset(p)
    assert len(back) == 0
    assert list(back.columns) == ["TI", "PY"]
    assert not list(tmp_path.glob("*.tmp~"))


# ── T11: concurrent migration does a SINGLE read (lost-update race closed) ───

def test_concurrent_migration_reads_legacy_exactly_once(tmp_path, monkeypatch):
    """If two threads resolve the same legacy xlsx at the same time, the migration
    must run ONLY ONCE under the lock. Without the lock, the slow thread would
    later publish a stale frame and silently undo a mutation that had meanwhile
    landed in the parquet (a major finding verified with a threaded repro)."""
    import threading as th
    from services import dataset_io

    src = tmp_path / "merged.xlsx"
    pd.DataFrame({"TI": ["a", "b"], "PY": [2020, 2021]}).to_excel(src, index=False)

    calls = {"n": 0}
    real_read = dataset_io.read_dataset

    def slow_read(path):
        calls["n"] += 1
        import time as _t
        _t.sleep(0.4)  # widen the read window — without the lock the 2nd thread would get in here
        return real_read(path)

    monkeypatch.setattr(dataset_io, "read_dataset", slow_read)

    results: list = [None, None]

    def worker(i):
        results[i] = dataset_io.ensure_parquet(src)

    t1 = th.Thread(target=worker, args=(0,))
    t2 = th.Thread(target=worker, args=(1,))
    t1.start(); t2.start(); t1.join(); t2.join()

    target = tmp_path / "merged.parquet"
    assert results[0] == target and results[1] == target
    assert calls["n"] == 1, "migrasyon kilidi ikinci okumayı engellemeli"
    assert len(real_read(target)) == 2
    assert not list(tmp_path.glob("*.tmp~"))


# ── T12: zombie legacy xlsx is healed on a later resolution ──────────────

def test_leftover_legacy_xlsx_is_healed_on_later_resolution(tmp_path):
    """If the move fails during the first migration (file open in Excel, AV scan),
    the xlsx stayed next to the parquet — forever: the UI shows two 'main datasets'
    and the xlsx silently goes stale. A later ensure_parquet call must retry the
    move."""
    from services import dataset_io

    src = tmp_path / "merged.xlsx"
    pd.DataFrame({"TI": ["old"]}).to_excel(src, index=False)
    target = tmp_path / "merged.parquet"
    dataset_io.atomic_write_dataset(pd.DataFrame({"TI": ["new", "er"]}), target)
    # State: both side by side (leftover of a failed move)

    out = dataset_io.ensure_parquet(src)
    assert out == target
    assert not src.exists(), "zombi xlsx snapshots/ altına taşınmalıydı"
    moved = list((tmp_path / "snapshots").glob("pre_parquet_migration_*.xlsx"))
    assert len(moved) == 1 and len(pd.read_excel(moved[0])) == 1
    # The parquet contents must be untouched (the move writes no data)
    assert len(dataset_io.read_dataset(target)) == 2
