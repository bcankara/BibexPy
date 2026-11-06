"""UID foundation: every row has a permanent, unique, SEQUENTIAL integer UID; row operations use ONLY it.

Covers the UID helpers (ensure_uid / check_uid / uid_key / write refusal), the UID birth points
(single-source analysis, legacy xlsx migration, lazy assignment on load), the high-water mark
(uid_max) that keeps a key from ever being issued twice, and the records API (delete by UID only,
update by UID only with an immutable UID, snapshot restore with the original keys).
"""

import asyncio
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

from services import dataset_io  # noqa: E402

def _assert_valid_uids(df: pd.DataFrame, expected_len: int | None = None) -> None:
    assert "UID" in df.columns
    assert df["UID"].dtype == "int64", df["UID"].dtype
    uids = [int(u) for u in df["UID"]]
    assert all(u >= 1 for u in uids), uids[:5]
    assert len(set(uids)) == len(uids)
    if expected_len is not None:
        assert len(uids) == expected_len


# ── ensure_uid / check_uid ──────────────────────────────────────────────

def test_ensure_uid_adds_a_missing_column():
    df = pd.DataFrame({"TI": ["a", "b", "c"]})
    assert dataset_io.ensure_uid(df) is True
    _assert_valid_uids(df, 3)
    assert list(df["UID"]) == [1, 2, 3]


def test_ensure_uid_fills_blanks_and_repairs_duplicates_but_keeps_valid_uids():
    df = pd.DataFrame({
        "UID": [5, "", None, 7, 7, "9", float("nan"), "  ", "r_old_style"],
        "TI": list("abcdefghi"),
    })
    assert dataset_io.ensure_uid(df) is True
    _assert_valid_uids(df, 9)
    # Valid unique keys are never changed (a numeric string counts as its number); the FIRST of a
    # repeated key keeps it. Everything else gets a NEW key above the largest valid one, in row order.
    assert list(df["UID"]) == [5, 10, 11, 7, 12, 9, 13, 14, 15]


def test_ensure_uid_is_a_noop_on_a_valid_frame_and_keeps_gaps():
    df = pd.DataFrame({"UID": [1, 2, 4, 5], "TI": list("abcd")})   # 3 was deleted earlier
    before = list(df["UID"])
    assert dataset_io.ensure_uid(df) is False
    assert list(df["UID"]) == before                                # gaps are NOT closed (a key is no position)


def test_ensure_uid_uses_the_floor_so_a_retired_key_is_never_reissued():
    df = pd.DataFrame({"UID": [1, 2, None], "TI": list("abc")})
    assert dataset_io.ensure_uid(df, floor=10) is True
    assert list(df["UID"]) == [1, 2, 11]
    # The floor never lowers anything: a frame whose own maximum is higher wins.
    df = pd.DataFrame({"UID": [40, None], "TI": list("ab")})
    dataset_io.ensure_uid(df, floor=10)
    assert list(df["UID"]) == [40, 41]
    assert dataset_io.max_uid(df) == 41


def test_ensure_uid_converts_the_column_to_int64():
    for raw, expect_changed in ((["1", "2", "3"], True), ([1.0, 2.0, 3.0], True), ([1, 2, 3], False)):
        df = pd.DataFrame({"UID": raw, "TI": list("abc")})
        changed = dataset_io.ensure_uid(df)
        assert list(df["UID"]) == [1, 2, 3] and df["UID"].dtype == "int64"
        assert changed is expect_changed                              # an int64 frame is left alone
    df = pd.DataFrame({"UID": [1.0, float("nan"), 3.0], "TI": list("abc")})   # float column after a concat
    assert dataset_io.ensure_uid(df) is True
    assert list(df["UID"]) == [1, 4, 3] and df["UID"].dtype == "int64"


def test_ensure_uid_handles_a_nullable_integer_column_with_missing_values():
    # A pandas nullable Int64 column holding <NA> must repair like a float column, not raise.
    df = pd.DataFrame({"UID": pd.array([1, None, 3], dtype="Int64"), "TI": list("abc")})
    assert dataset_io.ensure_uid(df) is True
    assert list(df["UID"]) == [1, 4, 3] and df["UID"].dtype == "int64"


def test_ensure_dataset_uid_reserves_keys_atomically_across_threads(client):
    # Two threads repairing frames of one analysis must never issue the same new key.
    import threading
    from services import analyses
    pid = client.post("/api/projects", json={"name": "uid-race"}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "single")
    analyses.mark_uid_scheme(pid, aid, 5)
    frames = [pd.DataFrame({"UID": [None] * 50, "TI": ["x"] * 50}) for _ in range(8)]
    threads = [threading.Thread(target=analyses.ensure_dataset_uid, args=(pid, f, aid)) for f in frames]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    issued = [int(v) for f in frames for v in f["UID"]]
    assert len(issued) == len(set(issued)) == 400
    assert min(issued) == 6 and analyses.get_uid_max(pid, aid) == max(issued) == 405


def test_ensure_uid_works_on_a_non_default_index():
    df = pd.DataFrame({"TI": list("abc")}, index=[10, 20, 30])
    assert dataset_io.ensure_uid(df) is True
    _assert_valid_uids(df, 3)
    df["UID"] = df["UID"].astype(object)
    df.loc[20, "UID"] = ""
    keep = (df.loc[10, "UID"], df.loc[30, "UID"])
    assert dataset_io.ensure_uid(df) is True
    assert (df.loc[10, "UID"], df.loc[30, "UID"]) == keep
    assert df.loc[20, "UID"] == 4
    _assert_valid_uids(df, 3)


def test_uid_key_is_one_canonical_text_for_every_spelling():
    for v in (12, 12.0, "12", " 12 ", "12.0"):
        assert dataset_io.uid_key(v) == "12", v
    import numpy as np
    assert dataset_io.uid_key(np.int64(12)) == "12" and dataset_io.uid_key(np.float64(12.0)) == "12"
    for blank in (None, "", "  ", "nan", float("nan")):
        assert dataset_io.uid_key(blank) == ""
    assert dataset_io.uid_key(" r_abc ") == "r_abc"                  # not a key, but still comparable


def test_new_uid_and_random_keys_are_gone():
    assert not hasattr(dataset_io, "new_uid")
    df = pd.DataFrame({"TI": list("abcdef")})
    dataset_io.ensure_uid(df)
    assert list(df["UID"]) == [1, 2, 3, 4, 5, 6]                     # no "r_" strings anywhere


def test_assign_sequential_uid_numbers_a_new_dataset_in_row_order():
    df = pd.DataFrame({"UID": ["old", "old"], "TI": list("ab")}, index=[5, 5])
    assert dataset_io.assign_sequential_uid(df) == 2
    assert list(df["UID"]) == [1, 2] and df["UID"].dtype == "int64"


def test_check_uid_raises_on_blank_and_duplicate():
    dataset_io.check_uid(pd.DataFrame({"TI": ["no uid column"]}))  # no column: no-op
    dataset_io.check_uid(pd.DataFrame({"UID": [1, 2]}))
    with pytest.raises(ValueError, match="uid_invariant_violation"):
        dataset_io.check_uid(pd.DataFrame({"UID": ["1", ""]}))
    with pytest.raises(ValueError, match="uid_invariant_violation"):
        dataset_io.check_uid(pd.DataFrame({"UID": [1, 1]}))
    with pytest.raises(ValueError, match="uid_invariant_violation"):
        dataset_io.check_uid(pd.DataFrame({"UID": [1, "1.0"]}))      # same key spelled twice
    with pytest.raises(ValueError, match="uid_invariant_violation"):
        dataset_io.check_uid(pd.DataFrame({"UID": [1.0, float("nan")]}))   # no NaN in a key column


def test_write_refuses_duplicate_or_blank_uids(tmp_path):
    target = tmp_path / "merged.parquet"
    for bad in ([1, 1], ["1", ""], [1, None]):
        with pytest.raises(ValueError, match="uid_invariant_violation"):
            dataset_io.atomic_write_dataset(pd.DataFrame({"UID": bad, "TI": ["x", "y"]}), target)
        assert not target.exists()
        assert not list(tmp_path.glob("*.tmp~"))
    # A frame without a UID column is still writable (legacy callers); a valid one too.
    dataset_io.atomic_write_dataset(pd.DataFrame({"TI": ["x"]}), target)
    dataset_io.atomic_write_dataset(pd.DataFrame({"UID": [1, 2], "TI": ["x", "y"]}), target)


# ── fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models")):
            sys.modules.pop(mod, None)
    from main import app  # noqa: E402
    return TestClient(app)


def _runner():
    return sys.modules["jobs.runner"].job_runner


def _make_project(client, df: pd.DataFrame, name: str = "UID") -> tuple[str, str, Path]:
    """Project with one finalized analysis whose merged.parquet is `df` (written verbatim)."""
    from services import analyses

    pid = client.post("/api/projects", json={"name": name}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "smart")
    if "UID" in df.columns:
        dataset_io.atomic_write_dataset(df, adir / "merged.parquet")
        analyses.mark_uid_scheme(pid, aid, dataset_io.max_uid(df))   # as a merge leaves it
    else:
        df.to_parquet(adir / "merged.parquet", index=False)  # bypass: a legacy, UID-less file
    analyses.finalize_analysis(pid, aid)
    return pid, aid, adir


def _uid_df(n: int = 6) -> pd.DataFrame:
    """Rows u0..u5 of the old tests are now the keys 1..6."""
    return pd.DataFrame({
        "UID": list(range(1, n + 1)),
        "TI": [f"Title {i}" for i in range(n)],
        "DI": ["10.1/dup", "10.1/dup"] + [f"10.1/x{i}" for i in range(2, n)],
        "PY": [2020 + i % 3 for i in range(n)],
    })


def _active_df(pid: str) -> pd.DataFrame:
    from services import analyses
    return dataset_io.read_dataset(analyses.active_dataset_path(pid))


# ── UID birth points ────────────────────────────────────────────────────

class _Ctx:
    cancelled = False

    def __init__(self):
        self.lines: list[str] = []

    def log(self, line):
        self.lines.append(line)

    def progress(self, value):
        pass


def test_single_source_analysis_writes_uids(client):
    from services import analyses, merger

    pid = client.post("/api/projects", json={"name": "Single"}).json()["id"]
    src = pd.DataFrame({"TI": ["a", "b", "c"], "DI": ["10.1/a", "10.1/b", ""], "PY": [2020, 2021, 2022]})
    aid, adir = analyses.create_analysis(pid, "single")
    asyncio.run(merger._finish_single_source(_Ctx(), pid, aid, adir, src, src, None))
    out = dataset_io.read_dataset(adir / "merged.parquet")
    _assert_valid_uids(out, 3)
    assert list(out["UID"]) == [1, 2, 3]
    assert list(out["TI"]) == ["a", "b", "c"]
    meta = analyses.get_analysis_meta(pid, aid)
    assert meta["uid_scheme"] == "seq_v1" and meta["uid_max"] == 3


def test_legacy_xlsx_migration_writes_uids(tmp_path):
    xlsx = tmp_path / "merged.xlsx"
    pd.DataFrame({"TI": ["a", "b", "c"], "PY": [2020, 2021, 2022]}).to_excel(xlsx, index=False)
    out = dataset_io.ensure_parquet(xlsx)
    assert out.suffix == ".parquet"
    migrated = dataset_io.read_dataset(out)
    _assert_valid_uids(migrated, 3)
    assert list(migrated["UID"]) == [1, 2, 3]


def test_legacy_xlsx_analysis_records_its_high_water_mark_when_migrated(client):
    from services import analyses

    pid = client.post("/api/projects", json={"name": "LegacyXlsx"}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "smart")
    pd.DataFrame({"TI": list("abc")}).to_excel(adir / "merged.xlsx", index=False)
    analyses.finalize_analysis(pid, aid)
    path = analyses.active_dataset_path(pid)                         # the lazy xlsx -> parquet migration
    assert path.suffix == ".parquet"
    assert list(dataset_io.read_dataset(path)["UID"]) == [1, 2, 3]
    assert analyses.get_uid_max(pid, aid) == 3


def test_legacy_xlsx_migration_keeps_existing_uids_and_repairs_duplicates(tmp_path):
    xlsx = tmp_path / "merged.xlsx"
    pd.DataFrame({"UID": [7, 9, 9], "TI": list("abc")}).to_excel(xlsx, index=False)
    out = dataset_io.read_dataset(dataset_io.ensure_parquet(xlsx))
    assert list(out["UID"]) == [7, 9, 10]


def test_load_merged_assigns_and_persists_uids_for_a_legacy_dataset(client):
    from services import filter_engine

    pid, _, adir = _make_project(client, pd.DataFrame({"TI": list("abcd")}))
    assert "UID" not in pd.read_parquet(adir / "merged.parquet").columns
    df = filter_engine.load_merged(pid)
    _assert_valid_uids(df, 4)
    on_disk = pd.read_parquet(adir / "merged.parquet")
    assert list(on_disk["UID"]) == list(df["UID"]) == [1, 2, 3, 4]
    from services import analyses
    assert analyses.get_uid_max(pid, analyses.get_active_analysis_id(pid)) == 4   # high-water mark follows
    # Stable: a second load (cache hit and a cold reload) returns the same UIDs.
    assert list(filter_engine.load_merged(pid)["UID"]) == list(df["UID"])
    filter_engine._DF_CACHE.clear()
    assert list(filter_engine.load_merged(pid)["UID"]) == list(df["UID"])


def test_load_merged_keeps_uids_in_memory_while_busy_and_persists_later(client, caplog):
    from services import filter_engine

    pid, _, adir = _make_project(client, pd.DataFrame({"TI": list("abc")}))
    with caplog.at_level("WARNING"):
        with _runner().mutation(pid):
            df = filter_engine.load_merged(pid)
            _assert_valid_uids(df, 3)
            # Busy: nothing was written, and the deferral is logged (not swallowed).
            assert "UID" not in pd.read_parquet(adir / "merged.parquet").columns
            # The same UIDs are served to every reader meanwhile.
            assert list(filter_engine.load_merged(pid)["UID"]) == list(df["UID"])
    assert any("UID persist deferred" in r.getMessage() for r in caplog.records)
    # Idle again: the next load persists exactly those UIDs.
    again = filter_engine.load_merged(pid)
    assert list(again["UID"]) == list(df["UID"])
    assert list(pd.read_parquet(adir / "merged.parquet")["UID"]) == list(df["UID"])


# ── records.delete: UIDs only ───────────────────────────────────────────

def test_delete_by_uids_only(client):
    pid, _, _ = _make_project(client, _uid_df())
    r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": ["2", "4", "ghost"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 2 and body["kept"] == 4
    assert body["requested"] == 3 and body["not_found"] == 1
    # The survivors keep their keys: deleting leaves gaps, nothing is renumbered.
    assert list(_active_df(pid)["UID"]) == [1, 3, 5, 6]


def test_delete_accepts_keys_as_numbers_and_text(client):
    pid, _, _ = _make_project(client, _uid_df())
    r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": [2, "3", " 4 ", 4.0]})
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 3 and r.json()["requested"] == 3
    assert list(_active_df(pid)["UID"]) == [1, 5, 6]


def test_delete_rejects_positions_and_dois(client):
    pid, _, _ = _make_project(client, _uid_df())
    for payload in ({"indices": [0, 1]}, {"dois": ["10.1/dup"]}, {"uids": []}, {"uids": ["", "  "]}, {}):
        r = client.post(f"/api/projects/{pid}/records/delete", json=payload)
        assert r.status_code == 400 and r.json()["detail"] == "uids_required", (payload, r.text)
    assert len(_active_df(pid)) == 6  # nothing was deleted
    # indices/dois next to uids are ignored: only the UID is acted on.
    r = client.post(f"/api/projects/{pid}/records/delete",
                    json={"uids": ["6"], "indices": [0], "dois": ["10.1/dup"]})
    assert r.status_code == 200 and r.json()["deleted"] == 1
    assert list(_active_df(pid)["UID"]) == [1, 2, 3, 4, 5]


def test_delete_of_unknown_uids_reports_them_and_changes_nothing(client):
    pid, _, _ = _make_project(client, _uid_df())
    r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": ["nope1", "nope2"]})
    assert r.status_code == 200
    assert r.json() == {"deleted": 0, "kept": 6, "snapshot": None, "requested": 2, "not_found": 2}
    assert len(_active_df(pid)) == 6


def test_delete_audit_counts_uids_only(client):
    from services import audit

    pid, _, _ = _make_project(client, _uid_df())
    client.post(f"/api/projects/{pid}/records/delete", json={"uids": ["1", "ghost"]})
    entry = next(e for e in audit.read(pid) if e.get("kind") == "records_delete")
    d = entry["details"]
    assert d["by_uid"] == 2 and d["not_found"] == 1 and d["deleted"] == 1
    assert "by_doi" not in d and "by_index" not in d


# ── records.update: UID only, UID immutable ─────────────────────────────

def test_update_by_uid(client):
    pid, _, _ = _make_project(client, _uid_df())
    r = client.post(f"/api/projects/{pid}/records/update", json={"uid": "3", "fields": {"TI": "EDITED"}})
    assert r.status_code == 200, r.text
    df = _active_df(pid)
    assert df.loc[df["UID"] == 3, "TI"].iloc[0] == "EDITED"
    assert (df["TI"] == "EDITED").sum() == 1
    # The key may also arrive as a JSON number.
    r = client.post(f"/api/projects/{pid}/records/update", json={"uid": 5, "fields": {"TI": "NUM"}})
    assert r.status_code == 200, r.text
    df = _active_df(pid)
    assert df.loc[df["UID"] == 5, "TI"].iloc[0] == "NUM" and (df["TI"] == "NUM").sum() == 1


def test_update_requires_a_uid(client):
    pid, _, _ = _make_project(client, _uid_df())
    for payload in ({"fields": {"TI": "x"}}, {"uid": "", "fields": {"TI": "x"}},
                    {"doi": "10.1/x3", "fields": {"TI": "x"}}):
        r = client.post(f"/api/projects/{pid}/records/update", json=payload)
        assert r.status_code == 400 and r.json()["detail"] == "uids_required", (payload, r.text)
    assert not (_active_df(pid)["TI"] == "x").any()


def test_update_unknown_uid_is_404_and_never_falls_back_to_the_doi(client):
    pid, _, _ = _make_project(client, _uid_df())
    r = client.post(f"/api/projects/{pid}/records/update",
                    json={"uid": "ghost", "doi": "10.1/x3", "fields": {"TI": "HIJACKED"}})
    assert r.status_code == 404 and r.json()["detail"] == "record_not_found"
    assert not (_active_df(pid)["TI"] == "HIJACKED").any()


def test_update_rejects_changing_the_uid(client):
    pid, _, _ = _make_project(client, _uid_df())
    for key in ("UID", "uid", " UID "):
        r = client.post(f"/api/projects/{pid}/records/update",
                        json={"uid": "2", "fields": {"TI": "x", key: "1"}})
        assert r.status_code == 400 and r.json()["detail"] == "uid_immutable", (key, r.text)
    df = _active_df(pid)
    assert list(df["UID"]) == [1, 2, 3, 4, 5, 6] and not (df["TI"] == "x").any()


def test_update_ignores_internal_columns(client):
    pid, _, _ = _make_project(client, _uid_df())
    r = client.post(f"/api/projects/{pid}/records/update",
                    json={"uid": "2", "fields": {"TI": "ok", "_norm_title": "evil"}})
    assert r.status_code == 200 and r.json()["fields"] == 1
    df = _active_df(pid)
    assert "_norm_title" not in df.columns and df.loc[df["UID"] == 2, "TI"].iloc[0] == "ok"
    # Only internal fields: nothing left to update.
    r = client.post(f"/api/projects/{pid}/records/update", json={"uid": "2", "fields": {"_x": "1"}})
    assert r.status_code == 400 and r.json()["detail"] == "no_fields_to_update"


# ── records.restore_snapshot ────────────────────────────────────────────

def _snapshot_rel(path: Path) -> str:
    return str(path.relative_to(sys.modules["config"].settings.storage_path))


def test_restore_of_a_uidless_snapshot_yields_valid_uids(client):
    pid, _, adir = _make_project(client, _uid_df())
    snap = adir / "snapshots" / "pre_delete_old.parquet"
    snap.parent.mkdir(exist_ok=True)
    pd.DataFrame({"TI": ["p", "q", "r"], "PY": [2001, 2002, 2003]}).to_parquet(snap, index=False)
    r = client.post(f"/api/projects/{pid}/records/restore-snapshot", json={"snapshot": _snapshot_rel(snap)})
    assert r.status_code == 200, r.text
    df = _active_df(pid)
    _assert_valid_uids(df, 3)
    assert list(df["TI"]) == ["p", "q", "r"]
    # New keys come from above the analysis's high-water mark (6): 1..6 may still sit in other snapshots.
    assert list(df["UID"]) == [7, 8, 9]
    from services import analyses
    assert analyses.get_uid_max(pid, analyses.get_active_analysis_id(pid)) == 9


def test_restore_repairs_duplicate_uids_in_a_snapshot(client):
    pid, _, adir = _make_project(client, _uid_df())
    snap = adir / "snapshots" / "pre_delete_dup.parquet"
    snap.parent.mkdir(exist_ok=True)
    pd.DataFrame({"UID": [2, 2, None], "TI": list("xyz")}).to_parquet(snap, index=False)
    r = client.post(f"/api/projects/{pid}/records/restore-snapshot", json={"snapshot": _snapshot_rel(snap)})
    assert r.status_code == 200, r.text
    df = _active_df(pid)
    assert list(df["UID"]) == [2, 7, 8] and len(df) == 3


def test_restore_keeps_the_uids_of_a_valid_snapshot(client):
    pid, _, adir = _make_project(client, _uid_df())
    r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": ["1", "2"]})
    snapshot = r.json()["snapshot"]
    assert list(_active_df(pid)["UID"]) == [3, 4, 5, 6]
    r = client.post(f"/api/projects/{pid}/records/restore-snapshot", json={"snapshot": snapshot})
    assert r.status_code == 200
    assert list(_active_df(pid)["UID"]) == [1, 2, 3, 4, 5, 6]       # the restored rows come back under their keys


def test_restore_rejects_a_snapshot_of_another_analysis(client):
    from services import analyses

    pid, first_id, first_dir = _make_project(client, _uid_df())
    foreign = first_dir / "snapshots" / "pre_delete_first.parquet"
    foreign.parent.mkdir(exist_ok=True)
    dataset_io.atomic_write_dataset(pd.DataFrame({"UID": [101, 102], "TI": ["foreign", "rows"]}), foreign)

    # A second analysis becomes the active one.
    second_id, second_dir = analyses.create_analysis(pid, "smart")
    dataset_io.atomic_write_dataset(_uid_df(3), second_dir / "merged.parquet")
    analyses.mark_uid_scheme(pid, second_id, 3)
    analyses.finalize_analysis(pid, second_id)
    assert analyses.get_active_analysis_id(pid) == second_id

    r = client.post(f"/api/projects/{pid}/records/restore-snapshot", json={"snapshot": _snapshot_rel(foreign)})
    assert r.status_code == 404 and "snapshot_not_found" in r.text
    assert list(_active_df(pid)["UID"]) == [1, 2, 3]  # untouched

    # Snapshots of the ACTIVE analysis are accepted.
    own = second_dir / "snapshots" / "pre_delete_second.parquet"
    own.parent.mkdir(exist_ok=True)
    dataset_io.atomic_write_dataset(_uid_df(2), own)
    r = client.post(f"/api/projects/{pid}/records/restore-snapshot", json={"snapshot": _snapshot_rel(own)})
    assert r.status_code == 200 and r.json()["restored"] == 2


# ── review additions: positional drift + index edge cases ──────────────

def test_ensure_uid_is_positional_with_a_repeated_index():
    # concat without ignore_index leaves repeated labels: a label-based repair would give
    # several rows the same new UID or overwrite a valid one.
    df = pd.concat([pd.DataFrame({"UID": [1, 1], "TI": list("ab")}),
                    pd.DataFrame({"UID": ["", 2], "TI": list("cd")})])
    assert df.index.tolist() == [0, 1, 0, 1]
    assert dataset_io.ensure_uid(df) is True
    assert list(df["UID"]) == [1, 3, 4, 2]
    bare = pd.concat([pd.DataFrame({"TI": list("ab")}), pd.DataFrame({"TI": list("cd")})])
    assert dataset_io.ensure_uid(bare) is True
    _assert_valid_uids(bare, 4)


def test_ensure_uid_leaves_valid_rows_untouched():
    df = pd.DataFrame({"UID": [5, 6, 6], "TI": list("abc")})
    dataset_io.ensure_uid(df)
    assert list(df["UID"]) == [5, 6, 7]


def test_a_later_delete_by_uid_hits_the_intended_row_after_earlier_deletes(client):
    """Positions shift after every delete; UIDs captured earlier still name the same rows."""
    pid, _, _ = _make_project(client, _uid_df(8))
    target_a, target_b = "6", "7"  # their positions are 5 and 6 right now
    r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": ["1", "2", "3"]})
    assert r.json()["deleted"] == 3
    # Row positions of the targets are now 2 and 3; a positional delete would hit the keys 3/4 rows.
    r = client.post(f"/api/projects/{pid}/records/update", json={"uid": target_a, "fields": {"TI": "EDITED"}})
    assert r.status_code == 200
    df = _active_df(pid)
    assert df.loc[df["UID"] == int(target_a), "TI"].iloc[0] == "EDITED" and (df["TI"] == "EDITED").sum() == 1
    r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": [target_b, "1"]})
    body = r.json()
    assert body["deleted"] == 1 and body["not_found"] == 1  # key 1 is already gone: skipped, never remapped
    assert list(_active_df(pid)["UID"]) == [4, 5, 6, 8]


def test_a_key_is_never_reused_after_deletes_and_repairs(client):
    """Rule 1 + 3: gaps stay, restored rows return under their old keys, repairs go above uid_max."""
    from services import analyses

    pid, aid, adir = _make_project(client, _uid_df(5))
    assert analyses.get_uid_max(pid, aid) == 5
    r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": ["4", "5"]})   # the TOP keys go
    assert r.status_code == 200
    snapshot = r.json()["snapshot"]
    assert list(_active_df(pid)["UID"]) == [1, 2, 3]
    assert analyses.get_uid_max(pid, aid) == 5                       # the high-water mark does not fall

    # A repair now (a row lost its key) must not hand out 4 or 5: they live on in the snapshot.
    broken = _active_df(pid)
    broken["UID"] = broken["UID"].astype(object)
    broken.loc[broken.index[-1], "UID"] = ""
    dataset_io.atomic_write_dataset(broken.assign(UID=[1, 2, 3]), adir / "merged.parquet")  # valid on disk ...
    healed = broken.copy()                                           # ... the in-memory copy is the broken one
    assert analyses.ensure_dataset_uid(pid, healed, analysis_id=aid) is True
    assert list(healed["UID"]) == [1, 2, 6]
    assert analyses.get_uid_max(pid, aid) == 6

    # Restoring the snapshot brings the deleted rows back with their ORIGINAL keys 4 and 5.
    r = client.post(f"/api/projects/{pid}/records/restore-snapshot", json={"snapshot": snapshot})
    assert r.status_code == 200
    assert list(_active_df(pid)["UID"]) == [1, 2, 3, 4, 5]
    assert analyses.get_uid_max(pid, aid) == 6


def test_uid_max_helpers_only_raise_the_mark(client):
    from services import analyses

    pid, aid, _ = _make_project(client, _uid_df(3))
    assert analyses.bump_uid_max(pid, aid, 10) == 10
    assert analyses.bump_uid_max(pid, aid, 4) == 10                  # never lowered
    assert analyses.get_uid_max(pid, aid) == 10
    assert analyses.get_uid_max(pid, "no_such_analysis") == 0
    meta = analyses.get_analysis_meta(pid, aid)
    assert meta["uid_scheme"] == "seq_v1" and meta["uid_max"] == 10
    assert meta.get("completed_at")                                  # the other meta fields survive a bump


def test_filter_apply_on_a_legacy_dataset_persists_uids_without_deferral(client, caplog):
    pid, _, adir = _make_project(client, pd.DataFrame({"TI": list("abcd"), "PY": [2018, 2019, 2020, 2021]}))
    with caplog.at_level("WARNING"):
        r = client.post(f"/api/projects/{pid}/filter/apply", json={"spec": {"year": {"min": 2020}}})
    assert r.status_code == 200 and r.json()["kept"] == 2, r.text
    assert not any("UID persist deferred" in x.getMessage() for x in caplog.records)
    kept = pd.read_parquet(adir / "merged.parquet")
    _assert_valid_uids(kept, 2)
    assert list(kept["UID"]) == [3, 4]                               # the survivors keep the keys they were given
