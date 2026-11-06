"""UID foundation: every row has a permanent, unique UID and row operations use ONLY the UID.

Covers the UID helpers (ensure_uid / check_uid / write refusal), the UID birth points
(single-source analysis, legacy xlsx migration, lazy assignment on load), and the records
API (delete by UID only, update by UID only with an immutable UID, snapshot restore).
"""

import asyncio
import re
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

from services import dataset_io  # noqa: E402

_UID_RE = re.compile(r"^r_[0-9a-f]{16}$")


def _assert_valid_uids(df: pd.DataFrame, expected_len: int | None = None) -> None:
    assert "UID" in df.columns
    uids = [str(u) for u in df["UID"]]
    assert all(_UID_RE.match(u) for u in uids), uids[:5]
    assert len(set(uids)) == len(uids)
    if expected_len is not None:
        assert len(uids) == expected_len


# ── ensure_uid / check_uid ──────────────────────────────────────────────

def test_ensure_uid_adds_a_missing_column():
    df = pd.DataFrame({"TI": ["a", "b", "c"]})
    assert dataset_io.ensure_uid(df) is True
    _assert_valid_uids(df, 3)


def test_ensure_uid_fills_blanks_and_repairs_duplicates_but_keeps_valid_uids():
    df = pd.DataFrame({
        "UID": ["keep_1", "", None, "dup", "dup", "keep_2", float("nan"), "  "],
        "TI": list("abcdefgh"),
    })
    assert dataset_io.ensure_uid(df) is True
    uids = [str(u) for u in df["UID"]]
    assert len(set(uids)) == 8
    # Valid unique UIDs are never changed; the FIRST of a repeated UID keeps it.
    assert uids[0] == "keep_1" and uids[5] == "keep_2" and uids[3] == "dup"
    assert uids[4] != "dup"
    for i in (1, 2, 4, 6, 7):
        assert _UID_RE.match(uids[i]), (i, uids[i])


def test_ensure_uid_is_a_noop_on_a_valid_frame():
    df = pd.DataFrame({"UID": ["r_" + "0" * 15 + str(i) for i in range(4)], "TI": list("abcd")})
    before = list(df["UID"])
    assert dataset_io.ensure_uid(df) is False
    assert list(df["UID"]) == before


def test_ensure_uid_works_on_a_non_default_index():
    df = pd.DataFrame({"TI": list("abc")}, index=[10, 20, 30])
    assert dataset_io.ensure_uid(df) is True
    _assert_valid_uids(df, 3)
    df.loc[20, "UID"] = ""
    keep = (df.loc[10, "UID"], df.loc[30, "UID"])
    assert dataset_io.ensure_uid(df) is True
    assert (df.loc[10, "UID"], df.loc[30, "UID"]) == keep
    _assert_valid_uids(df, 3)


def test_new_uid_format():
    assert _UID_RE.match(dataset_io.new_uid())
    assert dataset_io.new_uid() != dataset_io.new_uid()


def test_check_uid_raises_on_blank_and_duplicate():
    dataset_io.check_uid(pd.DataFrame({"TI": ["no uid column"]}))  # no column: no-op
    dataset_io.check_uid(pd.DataFrame({"UID": ["a", "b"]}))
    with pytest.raises(ValueError, match="uid_invariant_violation"):
        dataset_io.check_uid(pd.DataFrame({"UID": ["a", ""]}))
    with pytest.raises(ValueError, match="uid_invariant_violation"):
        dataset_io.check_uid(pd.DataFrame({"UID": ["a", "a"]}))


def test_write_refuses_duplicate_or_blank_uids(tmp_path):
    target = tmp_path / "merged.parquet"
    for bad in (["a", "a"], ["a", ""], ["a", None]):
        with pytest.raises(ValueError, match="uid_invariant_violation"):
            dataset_io.atomic_write_dataset(pd.DataFrame({"UID": bad, "TI": ["x", "y"]}), target)
        assert not target.exists()
        assert not list(tmp_path.glob("*.tmp~"))
    # A frame without a UID column is still writable (legacy callers); a valid one too.
    dataset_io.atomic_write_dataset(pd.DataFrame({"TI": ["x"]}), target)
    dataset_io.atomic_write_dataset(pd.DataFrame({"UID": ["a", "b"], "TI": ["x", "y"]}), target)


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
    else:
        df.to_parquet(adir / "merged.parquet", index=False)  # bypass: a legacy, UID-less file
    analyses.finalize_analysis(pid, aid)
    return pid, aid, adir


def _uid_df(n: int = 6) -> pd.DataFrame:
    return pd.DataFrame({
        "UID": [f"u{i}" for i in range(n)],
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
    assert list(out["TI"]) == ["a", "b", "c"]


def test_legacy_xlsx_migration_writes_uids(tmp_path):
    xlsx = tmp_path / "merged.xlsx"
    pd.DataFrame({"TI": ["a", "b", "c"], "PY": [2020, 2021, 2022]}).to_excel(xlsx, index=False)
    out = dataset_io.ensure_parquet(xlsx)
    assert out.suffix == ".parquet"
    _assert_valid_uids(dataset_io.read_dataset(out), 3)


def test_legacy_xlsx_migration_keeps_existing_uids_and_repairs_duplicates(tmp_path):
    xlsx = tmp_path / "merged.xlsx"
    pd.DataFrame({"UID": ["keep", "dup", "dup"], "TI": list("abc")}).to_excel(xlsx, index=False)
    out = dataset_io.read_dataset(dataset_io.ensure_parquet(xlsx))
    assert out["UID"].iloc[0] == "keep" and out["UID"].iloc[1] == "dup"
    assert out["UID"].is_unique and not out["UID"].isin(["", None]).any()


def test_load_merged_assigns_and_persists_uids_for_a_legacy_dataset(client):
    from services import filter_engine

    pid, _, adir = _make_project(client, pd.DataFrame({"TI": list("abcd")}))
    assert "UID" not in pd.read_parquet(adir / "merged.parquet").columns
    df = filter_engine.load_merged(pid)
    _assert_valid_uids(df, 4)
    on_disk = pd.read_parquet(adir / "merged.parquet")
    assert list(on_disk["UID"]) == list(df["UID"])
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
    r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": ["u1", "u3", "ghost"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 2 and body["kept"] == 4
    assert body["requested"] == 3 and body["not_found"] == 1
    assert list(_active_df(pid)["UID"]) == ["u0", "u2", "u4", "u5"]


def test_delete_rejects_positions_and_dois(client):
    pid, _, _ = _make_project(client, _uid_df())
    for payload in ({"indices": [0, 1]}, {"dois": ["10.1/dup"]}, {"uids": []}, {"uids": ["", "  "]}, {}):
        r = client.post(f"/api/projects/{pid}/records/delete", json=payload)
        assert r.status_code == 400 and r.json()["detail"] == "uids_required", (payload, r.text)
    assert len(_active_df(pid)) == 6  # nothing was deleted
    # indices/dois next to uids are ignored: only the UID is acted on.
    r = client.post(f"/api/projects/{pid}/records/delete",
                    json={"uids": ["u5"], "indices": [0], "dois": ["10.1/dup"]})
    assert r.status_code == 200 and r.json()["deleted"] == 1
    assert list(_active_df(pid)["UID"]) == ["u0", "u1", "u2", "u3", "u4"]


def test_delete_of_unknown_uids_reports_them_and_changes_nothing(client):
    pid, _, _ = _make_project(client, _uid_df())
    r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": ["nope1", "nope2"]})
    assert r.status_code == 200
    assert r.json() == {"deleted": 0, "kept": 6, "snapshot": None, "requested": 2, "not_found": 2}
    assert len(_active_df(pid)) == 6


def test_delete_audit_counts_uids_only(client):
    from services import audit

    pid, _, _ = _make_project(client, _uid_df())
    client.post(f"/api/projects/{pid}/records/delete", json={"uids": ["u0", "ghost"]})
    entry = next(e for e in audit.read(pid) if e.get("kind") == "records_delete")
    d = entry["details"]
    assert d["by_uid"] == 2 and d["not_found"] == 1 and d["deleted"] == 1
    assert "by_doi" not in d and "by_index" not in d


# ── records.update: UID only, UID immutable ─────────────────────────────

def test_update_by_uid(client):
    pid, _, _ = _make_project(client, _uid_df())
    r = client.post(f"/api/projects/{pid}/records/update", json={"uid": "u2", "fields": {"TI": "EDITED"}})
    assert r.status_code == 200, r.text
    df = _active_df(pid)
    assert df.loc[df["UID"] == "u2", "TI"].iloc[0] == "EDITED"
    assert (df["TI"] == "EDITED").sum() == 1


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
                        json={"uid": "u1", "fields": {"TI": "x", key: "u0"}})
        assert r.status_code == 400 and r.json()["detail"] == "uid_immutable", (key, r.text)
    df = _active_df(pid)
    assert list(df["UID"]) == [f"u{i}" for i in range(6)] and not (df["TI"] == "x").any()


def test_update_ignores_internal_columns(client):
    pid, _, _ = _make_project(client, _uid_df())
    r = client.post(f"/api/projects/{pid}/records/update",
                    json={"uid": "u1", "fields": {"TI": "ok", "_norm_title": "evil"}})
    assert r.status_code == 200 and r.json()["fields"] == 1
    df = _active_df(pid)
    assert "_norm_title" not in df.columns and df.loc[df["UID"] == "u1", "TI"].iloc[0] == "ok"
    # Only internal fields: nothing left to update.
    r = client.post(f"/api/projects/{pid}/records/update", json={"uid": "u1", "fields": {"_x": "1"}})
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


def test_restore_repairs_duplicate_uids_in_a_snapshot(client):
    pid, _, adir = _make_project(client, _uid_df())
    snap = adir / "snapshots" / "pre_delete_dup.parquet"
    snap.parent.mkdir(exist_ok=True)
    pd.DataFrame({"UID": ["same", "same", ""], "TI": list("xyz")}).to_parquet(snap, index=False)
    r = client.post(f"/api/projects/{pid}/records/restore-snapshot", json={"snapshot": _snapshot_rel(snap)})
    assert r.status_code == 200, r.text
    df = _active_df(pid)
    assert df["UID"].iloc[0] == "same" and df["UID"].is_unique and len(df) == 3


def test_restore_keeps_the_uids_of_a_valid_snapshot(client):
    pid, _, adir = _make_project(client, _uid_df())
    r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": ["u0", "u1"]})
    snapshot = r.json()["snapshot"]
    assert len(_active_df(pid)) == 4
    r = client.post(f"/api/projects/{pid}/records/restore-snapshot", json={"snapshot": snapshot})
    assert r.status_code == 200
    assert list(_active_df(pid)["UID"]) == [f"u{i}" for i in range(6)]


def test_restore_rejects_a_snapshot_of_another_analysis(client):
    from services import analyses

    pid, first_id, first_dir = _make_project(client, _uid_df())
    foreign = first_dir / "snapshots" / "pre_delete_first.parquet"
    foreign.parent.mkdir(exist_ok=True)
    dataset_io.atomic_write_dataset(pd.DataFrame({"UID": ["f1", "f2"], "TI": ["foreign", "rows"]}), foreign)

    # A second analysis becomes the active one.
    second_id, second_dir = analyses.create_analysis(pid, "smart")
    dataset_io.atomic_write_dataset(_uid_df(3), second_dir / "merged.parquet")
    analyses.finalize_analysis(pid, second_id)
    assert analyses.get_active_analysis_id(pid) == second_id

    r = client.post(f"/api/projects/{pid}/records/restore-snapshot", json={"snapshot": _snapshot_rel(foreign)})
    assert r.status_code == 404 and "snapshot_not_found" in r.text
    assert list(_active_df(pid)["UID"]) == ["u0", "u1", "u2"]  # untouched

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
    df = pd.concat([pd.DataFrame({"UID": ["x", "x"], "TI": list("ab")}),
                    pd.DataFrame({"UID": ["", "y"], "TI": list("cd")})])
    assert df.index.tolist() == [0, 1, 0, 1]
    assert dataset_io.ensure_uid(df) is True
    uids = list(df["UID"])
    assert uids[0] == "x" and uids[3] == "y"
    assert len(set(uids)) == 4 and _UID_RE.match(uids[1]) and _UID_RE.match(uids[2])
    bare = pd.concat([pd.DataFrame({"TI": list("ab")}), pd.DataFrame({"TI": list("cd")})])
    assert dataset_io.ensure_uid(bare) is True
    _assert_valid_uids(bare, 4)


def test_ensure_uid_leaves_untouched_rows_byte_identical():
    df = pd.DataFrame({"UID": [" padded ", "dup", "dup"], "TI": list("abc")})
    dataset_io.ensure_uid(df)
    assert df["UID"].iloc[0] == " padded " and df["UID"].iloc[1] == "dup"


def test_a_later_delete_by_uid_hits_the_intended_row_after_earlier_deletes(client):
    """Positions shift after every delete; UIDs captured earlier still name the same rows."""
    pid, _, _ = _make_project(client, _uid_df(8))
    target_a, target_b = "u5", "u6"  # their positions are 5 and 6 right now
    r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": ["u0", "u1", "u2"]})
    assert r.json()["deleted"] == 3
    # Row positions of the targets are now 2 and 3; a positional delete would hit u2/u3 territory.
    r = client.post(f"/api/projects/{pid}/records/update", json={"uid": target_a, "fields": {"TI": "EDITED"}})
    assert r.status_code == 200
    df = _active_df(pid)
    assert df.loc[df["UID"] == target_a, "TI"].iloc[0] == "EDITED" and (df["TI"] == "EDITED").sum() == 1
    r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": [target_b, "u0"]})
    body = r.json()
    assert body["deleted"] == 1 and body["not_found"] == 1  # u0 is already gone: skipped, never remapped
    assert list(_active_df(pid)["UID"]) == ["u3", "u4", "u5", "u7"]


def test_filter_apply_on_a_legacy_dataset_persists_uids_without_deferral(client, caplog):
    pid, _, adir = _make_project(client, pd.DataFrame({"TI": list("abcd"), "PY": [2018, 2019, 2020, 2021]}))
    with caplog.at_level("WARNING"):
        r = client.post(f"/api/projects/{pid}/filter/apply", json={"spec": {"year": {"min": 2020}}})
    assert r.status_code == 200 and r.json()["kept"] == 2, r.text
    assert not any("UID persist deferred" in x.getMessage() for x in caplog.records)
    _assert_valid_uids(pd.read_parquet(adir / "merged.parquet"), 2)
