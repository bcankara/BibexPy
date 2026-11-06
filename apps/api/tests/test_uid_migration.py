"""One-time conversion of existing analyses to sequential row keys (services/uid_migration).

A storage written by the previous version is rebuilt on disk (random "r_..." keys in the
dataset, in snapshots, in the borderline queue, in uid_v1 proposals and in the DOI
provenance log), then the migration runs and every key is checked.
"""

import csv
import json
import sys
import threading
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models")):
            sys.modules.pop(mod, None)
    from main import app  # noqa: E402
    return TestClient(app)


def _mig():
    from services import uid_migration
    uid_migration.reset_status()
    return uid_migration


def _dio():
    from services import dataset_io
    return dataset_io


# ── legacy storage builders (bypass dataset_io writers: they validate the NEW key format) ──

CUR = ["r_a1", "r_a2", "r_a3", "r_a4", "r_a5", "r_a6"]      # rows of the current dataset
GONE = ["r_g7", "r_g8"]                                      # deleted after the snapshots were taken


def _frame(keys, tag="T"):
    return pd.DataFrame({
        "UID": list(keys),
        "TI": [f"{tag} {k}" for k in keys],
        "PY": list(range(2000, 2000 + len(keys))),
    })


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _write_xlsx(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(path, index=False)


def _new_analysis(client, name="Legacy", method="smart"):
    """A finalized analysis whose meta predates the key scheme."""
    from services import analyses
    pid = client.post("/api/projects", json={"name": name}).json()["id"]
    aid, adir = analyses.create_analysis(pid, method)
    analyses.finalize_analysis(pid, aid)
    _strip_scheme(adir)
    return pid, aid, adir


def _strip_scheme(adir: Path) -> None:
    from services import analyses
    meta = analyses._read_meta(adir)
    meta.pop("uid_scheme", None)
    meta.pop("uid_max", None)
    analyses._write_meta(adir, meta)


def _legacy_analysis(client, name="Legacy"):
    """Dataset + snapshots + borderline queue + audit + proposals + provenance, all with r_ keys."""
    pid, aid, adir = _new_analysis(client, name)
    _write_parquet(_frame(CUR), adir / "merged.parquet")
    # Snapshot 1 (parquet): the dataset plus two rows that were deleted afterwards.
    _write_parquet(_frame(CUR + GONE), adir / "snapshots" / "pre_delete_20260101_000001_aaaaaa.parquet")
    # Snapshot 2 (parquet): a subset, taken later, in another order.
    _write_parquet(_frame(["r_a3", "r_g8", "r_a1"]), adir / "snapshots" / "pre_filter_20260101_000002_bbbbbb.parquet")
    # Snapshot 3 (legacy workbook).
    _write_xlsx(_frame(["r_a2", "r_g7", "r_a6"]), adir / "snapshots" / "pre_parquet_migration_20250101_000000_cccccc.xlsx")
    # Borderline queue: one pair of current rows and one pair with a deleted row.
    _write_xlsx(pd.DataFrame({
        "pair_id": ["P1", "P2"],
        "wos_uid": ["r_a1", "r_g7"],
        "scp_uid": ["r_a4", "r_a2"],
        "wos_title": ["x", "y"],
    }), adir / "borderline_queue.xlsx")
    # Match audit (wos_uid/scp_uid of the pair that was merged into one row).
    _write_xlsx(pd.DataFrame({
        "pair_id": ["M1", "M2"],
        "wos_uid": ["r_a3", "r_g8"],
        "scp_uid": ["r_a5", "r_zz"],
        "doi": ["10.1/a", "10.1/b"],
    }), adir / "match_audit.xlsx")
    _write_xlsx(_frame(["r_a6", "r_a5"], tag="L"), adir / "Lost_Wos_Records.xlsx")
    # Proposals: uid_v1 (keys) and one old position-based file (no schema).
    (adir / "disambiguation_authors.json").write_text(json.dumps({
        "kind": "authors", "schema": "uid_v1", "analysis_id": aid,
        "clusters": [{"cluster_id": "c1", "records": ["r_a1", "r_a2"],
                      "members": [{"id": "m1", "records": ["r_a3", "r_g7"]}]}],
        "splits": [{"split_id": "s1", "groups": [{"records": ["r_a4"]}, {"records": ["r_a5", "r_a6"]}]}],
        "uncertain": [],
    }), encoding="utf-8")
    (adir / "disambiguation_affiliations.json").write_text(json.dumps({
        "kind": "affiliations", "clusters": [{"cluster_id": "c9", "records": [0, 3, 5]}], "uncertain": [],
    }), encoding="utf-8")
    # Provenance log of a DOI pass.
    with open(adir / "doi_lookup_20260101_000000.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["UID", "record_title", "outcome", "duplicate_of"])
        w.writeheader()
        w.writerow({"UID": "r_a2", "record_title": "t", "outcome": "written", "duplicate_of": ""})
        w.writerow({"UID": "r_a5", "record_title": "u", "outcome": "duplicate", "duplicate_of": "r_a1"})
    return pid, aid, adir


def _keys(path: Path) -> list:
    return list(_dio().read_dataset(path)["UID"])


def _snap(adir: Path, name: str) -> Path:
    return adir / "snapshots" / name


S1 = "pre_delete_20260101_000001_aaaaaa.parquet"
S2 = "pre_filter_20260101_000002_bbbbbb.parquet"
S3 = "pre_parquet_migration_20250101_000000_cccccc.xlsx"


# ── the main scenario ───────────────────────────────────────────────────

def test_migration_converts_dataset_snapshots_and_references(client):
    from services import analyses, audit
    pid, aid, adir = _legacy_analysis(client)
    mig = _mig()

    status = mig.run_migration()
    assert status["state"] == "done" and status["errors"] == []
    assert status["projects_total"] == 1 and status["projects_done"] == 1
    assert status["analyses_total"] == 1 and status["analyses_done"] == 1

    # Dataset: 1..N in the CURRENT order, as int64.
    ds = _dio().read_dataset(adir / "merged.parquet")
    assert list(ds["UID"]) == [1, 2, 3, 4, 5, 6]
    assert str(ds["UID"].dtype) == "int64"
    assert list(ds["TI"]) == [f"T {k}" for k in CUR]          # no other column touched

    # Snapshot 1: the current rows keep the dataset's numbers, deleted rows come AFTER N.
    s1 = _dio().read_dataset(_snap(adir, S1))
    assert list(s1["UID"]) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert list(s1["TI"]) == [f"T {k}" for k in CUR + GONE]
    # Snapshot 2: consistent with snapshot 1 (r_g8 -> 8), whatever the row order.
    s2 = _dio().read_dataset(_snap(adir, S2))
    assert list(s2["UID"]) == [3, 8, 1]
    # Legacy workbook snapshot keeps its name and format and uses the same numbers.
    s3 = _dio().read_dataset(_snap(adir, S3))
    assert list(s3["UID"]) == [2, 7, 6]
    assert str(s3["UID"].dtype) == "int64"

    # A row deleted after a snapshot: restoring that snapshot brings it back under its own key,
    # and that key is not used by any current row.
    deleted_row = s1[s1["TI"] == "T r_g7"].iloc[0]
    assert int(deleted_row["UID"]) == 7 and 7 not in set(ds["UID"])

    # References.
    bq = pd.read_excel(adir / "borderline_queue.xlsx")
    assert [dataset_str(v) for v in bq["wos_uid"]] == ["1", "7"]
    assert [dataset_str(v) for v in bq["scp_uid"]] == ["4", "2"]
    ma = pd.read_excel(adir / "match_audit.xlsx")
    assert "wos_uid" not in ma.columns and "scp_uid" not in ma.columns
    assert [dataset_str(v) for v in ma["uid"]] == ["3", "8"]      # the WoS row survives a merge
    lost = pd.read_excel(adir / "Lost_Wos_Records.xlsx")
    assert [dataset_str(v) for v in lost["UID"]] == ["6", "5"]

    props = json.loads((adir / "disambiguation_authors.json").read_text(encoding="utf-8"))
    assert props["schema"] == "uid_v2"
    assert props["clusters"][0]["records"] == [1, 2]
    assert props["clusters"][0]["members"][0]["records"] == [3, 7]
    assert props["splits"][0]["groups"][0]["records"] == [4]
    assert props["splits"][0]["groups"][1]["records"] == [5, 6]
    # Position-based file: untouched (and still refused by the apply path).
    old = json.loads((adir / "disambiguation_affiliations.json").read_text(encoding="utf-8"))
    assert "schema" not in old and old["clusters"][0]["records"] == [0, 3, 5]

    with open(adir / "doi_lookup_20260101_000000.csv", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["UID"] for r in rows] == ["2", "5"]
    assert [r["duplicate_of"] for r in rows] == ["", "1"]

    # Meta is written, the plan file is gone, the history has one line.
    meta = analyses.get_analysis_meta(pid, aid)
    assert meta["uid_scheme"] == "seq_v1" and meta["uid_max"] == 8
    assert meta["id"] == aid and meta.get("method") == "smart"      # nothing else lost
    assert not (adir / "uid_migration_plan.json").exists()
    entries = [e for e in audit.read(pid) if e.get("title_key") == "audit.titles.uidMigration"]
    assert len(entries) == 1
    assert entries[0]["kind"] == "format_migration" and entries[0]["analysis_id"] == aid
    assert entries[0]["title_params"] == {"analysis": aid, "records": 6}


def dataset_str(v) -> str:
    return _dio().uid_key(v)


def test_second_run_is_a_noop(client):
    _, _, adir = _legacy_analysis(client)
    mig = _mig()
    mig.run_migration()
    before = {p.name: p.read_bytes() for p in [adir / "merged.parquet", _snap(adir, S1), _snap(adir, S2),
                                              adir / "borderline_queue.xlsx", adir / "meta.json"]}
    status = mig.run_migration()
    assert status["state"] == "done"
    assert status["projects_total"] == 0 and status["analyses_total"] == 0
    for p in [adir / "merged.parquet", _snap(adir, S1), _snap(adir, S2),
              adir / "borderline_queue.xlsx", adir / "meta.json"]:
        assert p.read_bytes() == before[p.name], p.name


def test_endpoint_reports_status_and_fast_path(client):
    mig = _mig()
    r = client.get("/api/system/uid-migration")
    assert r.status_code == 200 and r.json()["state"] == "idle"
    mig.run_migration()                                   # empty storage: straight to done
    body = client.get("/api/system/uid-migration").json()
    assert body["state"] == "done" and body["projects_total"] == 0 and body["errors"] == []
    for field in ("projects_done", "analyses_done", "current_project_name", "error"):
        assert field in body


# ── robustness ──────────────────────────────────────────────────────────

def test_one_broken_analysis_does_not_stop_the_others(client):
    from services import analyses
    pid, aid, adir = _legacy_analysis(client, "Good")
    # Same project, second analysis: an unreadable dataset.
    bad_id, bad_dir = analyses.create_analysis(pid, "smart")
    analyses.finalize_analysis(pid, bad_id)
    _strip_scheme(bad_dir)
    (bad_dir / "merged.parquet").write_bytes(b"this is not a parquet file")
    # Another project that is fine.
    pid2, aid2, adir2 = _legacy_analysis(client, "Other")

    status = _mig().run_migration()
    assert status["state"] == "error" and status["error"]
    assert [(e["analysis_id"], e["project_id"]) for e in status["errors"]] == [(bad_id, pid)]
    assert status["errors"][0]["project_name"] == "Good" and status["errors"][0]["message"]
    assert status["analyses_total"] == 3 and status["analyses_done"] == 2
    assert status["projects_done"] == 2

    for d in (adir, adir2):
        assert _keys(d / "merged.parquet") == [1, 2, 3, 4, 5, 6]
    assert analyses.get_analysis_meta(pid, aid)["uid_scheme"] == "seq_v1"
    # The broken one keeps its old state (no marker) and the project is released again.
    assert "uid_scheme" not in analyses.get_analysis_meta(pid, bad_id)
    assert not sys.modules["jobs.runner"].job_runner.mutation_in_flight(pid)
    assert not sys.modules["jobs.runner"].job_runner.mutation_in_flight(pid2)


def test_interrupted_run_resumes_with_the_same_numbers(client, monkeypatch):
    pid, aid, adir = _legacy_analysis(client)
    mig = _mig()

    def boom(*a, **k):
        raise RuntimeError("simulated crash after the datasets were rewritten")

    monkeypatch.setattr(mig, "_translate_references", boom)
    status = mig.run_migration()
    assert status["state"] == "error"
    # Dataset and snapshots are already converted, the marker is not there yet.
    assert _keys(adir / "merged.parquet") == [1, 2, 3, 4, 5, 6]
    assert list(_dio().read_dataset(_snap(adir, S1))["UID"]) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert (adir / "uid_migration_plan.json").exists()
    assert "r_a1" in set(pd.read_excel(adir / "borderline_queue.xlsx")["wos_uid"])

    monkeypatch.undo()
    status = mig.run_migration()
    assert status["state"] == "done", status
    # The references are translated with the SAME map even though the old keys are gone
    # from the dataset and the snapshots.
    bq = pd.read_excel(adir / "borderline_queue.xlsx")
    assert [dataset_str(v) for v in bq["wos_uid"]] == ["1", "7"]
    assert [dataset_str(v) for v in bq["scp_uid"]] == ["4", "2"]
    assert list(_dio().read_dataset(_snap(adir, S2))["UID"]) == [3, 8, 1]
    from services import analyses
    meta = analyses.get_analysis_meta(pid, aid)
    assert meta["uid_scheme"] == "seq_v1" and meta["uid_max"] == 8
    assert not (adir / "uid_migration_plan.json").exists()


def test_blank_and_repeated_keys_get_fresh_numbers(client):
    pid, aid, adir = _new_analysis(client)
    _write_parquet(pd.DataFrame({"UID": ["r_x", "", "r_x", "r_y"], "TI": list("abcd")}), adir / "merged.parquet")
    _write_parquet(pd.DataFrame({"UID": ["r_y", "", "r_q", "r_q"], "TI": list("efgh")}),
                   adir / "snapshots" / "pre_x_1_a.parquet")
    status = _mig().run_migration()
    assert status["state"] == "done", status
    assert _keys(adir / "merged.parquet") == [1, 2, 3, 4]          # always 1..N by position
    snap = _keys(adir / "snapshots" / "pre_x_1_a.parquet")
    # r_y -> 4 (the dataset's number); blank, r_q and the repeat of r_q get new numbers > N.
    assert snap[0] == 4 and len(set(snap)) == 4 and min(snap[1:]) > 4
    from services import analyses
    assert analyses.get_analysis_meta(pid, aid)["uid_max"] == max(snap)


def test_legacy_workbook_dataset_becomes_parquet_with_sequential_keys(client):
    pid, aid, adir = _new_analysis(client, method="single")
    _write_xlsx(pd.DataFrame({"TI": ["a", "b", "c"], "PY": [2001, 2002, 2003]}), adir / "merged.xlsx")
    status = _mig().run_migration()
    assert status["state"] == "done", status
    assert (adir / "merged.parquet").exists() and not (adir / "merged.xlsx").exists()
    df = _dio().read_dataset(adir / "merged.parquet")
    assert list(df["UID"]) == [1, 2, 3] and list(df["TI"]) == ["a", "b", "c"]
    # The original workbook is kept as a rollback snapshot (it predates keys; left as it is).
    moved = list((adir / "snapshots").glob("pre_parquet_migration_*.xlsx"))
    assert len(moved) == 1
    assert "UID" not in pd.read_excel(moved[0]).columns


def test_already_sequential_analysis_only_gets_its_marker(client):
    pid, aid, adir = _new_analysis(client)
    df = pd.DataFrame({"UID": [1, 2, 4, 5], "TI": list("abcd")})       # a gap: row 3 was deleted
    _write_parquet(df, adir / "merged.parquet")
    before = (adir / "merged.parquet").read_bytes()
    status = _mig().run_migration()
    assert status["state"] == "done"
    assert (adir / "merged.parquet").read_bytes() == before            # not rewritten
    from services import analyses
    meta = analyses.get_analysis_meta(pid, aid)
    assert meta["uid_scheme"] == "seq_v1" and meta["uid_max"] == 5


def test_snapshot_without_keys_is_left_alone_and_unfinished_merge_is_skipped(client):
    from services import analyses
    pid, aid, adir = _new_analysis(client)
    _write_parquet(_frame(CUR), adir / "merged.parquet")
    _write_parquet(pd.DataFrame({"TI": ["old"]}), adir / "snapshots" / "pre_old_1_a.parquet")
    # A merge that never finished.
    run_id, run_dir = analyses.create_analysis(pid, "smart")
    _write_parquet(_frame(["r_half"]), run_dir / "merged.parquet")
    status = _mig().run_migration()
    assert status["state"] == "done" and status["analyses_total"] == 1
    assert "UID" not in pd.read_parquet(adir / "snapshots" / "pre_old_1_a.parquet").columns
    assert list(pd.read_parquet(run_dir / "merged.parquet")["UID"]) == ["r_half"]
    assert "uid_scheme" not in analyses._read_meta(run_dir)


def test_soft_deleted_analysis_is_skipped(client):
    from services import analyses
    pid, aid, adir = _legacy_analysis(client)
    meta = analyses._read_meta(adir)
    meta["deleted"] = True
    analyses._write_meta(adir, meta)
    status = _mig().run_migration()
    assert status["analyses_total"] == 0
    assert list(pd.read_parquet(adir / "merged.parquet")["UID"]) == CUR


# ── the project is held while it is converted ───────────────────────────

def test_project_is_locked_during_the_background_conversion(client, monkeypatch):
    pid, aid, adir = _legacy_analysis(client)
    mig = _mig()
    runner = sys.modules["jobs.runner"].job_runner
    entered, release = threading.Event(), threading.Event()
    real = mig.migrate_analysis

    def slow(project_id, a):
        entered.set()
        assert release.wait(10)
        return real(project_id, a)

    monkeypatch.setattr(mig, "migrate_analysis", slow)
    mig.start_background()
    try:
        # Held from the moment start_background() returns, i.e. before the first request.
        assert runner.mutation_in_flight(pid)
        assert entered.wait(10)
        status = client.get("/api/system/uid-migration").json()
        assert status["state"] == "running" and status["current_project_id"] == pid
        # A mutating request is refused while the conversion holds the project.
        r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": ["1"]})
        assert r.status_code == 409
    finally:
        release.set()
    for _ in range(200):
        if mig.get_status()["state"] != "running":
            break
        threading.Event().wait(0.05)
    assert mig.get_status()["state"] == "done"
    assert not runner.mutation_in_flight(pid)
    assert _keys(adir / "merged.parquet") == [1, 2, 3, 4, 5, 6]


# ── review fixes ────────────────────────────────────────────────────────

def test_unreadable_snapshot_does_not_block_the_analysis(client):
    from services import analyses
    pid, aid, adir = _legacy_analysis(client)
    bad = _snap(adir, "zz_broken.parquet")
    bad.write_bytes(b"junk")
    status = _mig().run_migration()
    assert status["state"] == "done" and status["errors"] == [], status
    assert _keys(adir / "merged.parquet") == [1, 2, 3, 4, 5, 6]
    assert list(_dio().read_dataset(_snap(adir, S1))["UID"]) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert bad.read_bytes() == b"junk"                                   # left exactly as it was
    assert analyses.get_analysis_meta(pid, aid)["uid_scheme"] == "seq_v1"


def test_sequential_keys_stored_as_floats_or_text_become_int64(client):
    pid, aid, adir = _new_analysis(client)
    _write_parquet(pd.DataFrame({"UID": [1.0, 2.0, 4.0], "TI": list("abc")}), adir / "merged.parquet")
    _write_parquet(pd.DataFrame({"UID": ["4", "7"], "TI": list("cz")}), adir / "snapshots" / "s_1.parquet")
    status = _mig().run_migration()
    assert status["state"] == "done", status
    ds = pd.read_parquet(adir / "merged.parquet")
    assert str(ds["UID"].dtype) == "int64" and list(ds["UID"]) == [1, 2, 4]
    snap = pd.read_parquet(adir / "snapshots" / "s_1.parquet")
    assert str(snap["UID"].dtype) == "int64" and list(snap["UID"]) == [4, 7]
    from services import analyses
    assert analyses.get_analysis_meta(pid, aid)["uid_max"] == 7


def test_uid_max_is_never_lowered(client):
    from services import analyses
    pid, aid, adir = _legacy_analysis(client)
    meta = analyses._read_meta(adir)
    meta["uid_max"] = 500
    analyses._write_meta(adir, meta)
    assert _mig().run_migration()["state"] == "done"
    meta = analyses.get_analysis_meta(pid, aid)
    assert meta["uid_scheme"] == "seq_v1" and meta["uid_max"] == 500


def test_failed_analysis_raises_the_key_floor_but_not_the_marker(client, monkeypatch):
    from services import analyses
    pid, aid, adir = _legacy_analysis(client)
    mig = _mig()
    monkeypatch.setattr(mig, "_translate_references",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert mig.run_migration()["state"] == "error"
    meta = analyses._read_meta(adir)
    assert "uid_scheme" not in meta
    assert meta["uid_max"] == 8          # nothing issued by normal use can land in 1..8 again
