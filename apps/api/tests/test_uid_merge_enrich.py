"""UID everywhere: Smart Merge, borderline review and enrichment.

Pins the identity rules of package U3:

* While matching, the source rows carry TEMPORARY ids (never persisted); the permanent keys
  1..N are assigned in the final row order (merged pairs, unmatched WoS, unmatched Scopus) and
  the merge result is unchanged (bundled sample: 3735 records, 422 matched pairs, 20 borderline).
* match_audit.xlsx (`uid`), borderline_queue.xlsx (wos_uid/scp_uid) and Lost_*_Records.xlsx
  are translated to the final keys before they are written.
* Accepting a borderline pair locates the rows by UID first, never by position, and a
  UID that is no longer in the dataset is reported instead of mapped to another row.
* The enrichment jobs persist UIDs before their snapshot and never write 'row N' labels.
"""

import asyncio
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))
_PACKAGES = _API_ROOT.parents[1] / "packages"
if str(_PACKAGES) not in sys.path:
    sys.path.insert(0, str(_PACKAGES))

from services.smart_merger import (  # noqa: E402
    _assign_final_keys,
    _assign_source_ids,
    _merge_accepted_pairs,
    merge_pair_with_preferences,
)

_SAMPLE = _API_ROOT.parents[1] / "python_pkg" / "src" / "bibexpy" / "_samples" / "simple_project"


# ── helpers ──────────────────────────────────────────────────────────────

def _wait_job(client, job_id, tries=300):
    for _ in range(tries):
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("completed", "failed", "cancelled"):
            return j
        time.sleep(1)
    return j


@pytest.fixture
def sample_client(monkeypatch, tmp_path):
    """App with startup seeding of the bundled sample project."""
    monkeypatch.setenv("BIBEXPY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("BIBEXPY_SAMPLES_DIR", str(_SAMPLE))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as c:
        yield c


def _merged_sample(client):
    """Run Smart Merge on the bundled sample project; return (project id, job result)."""
    projects = client.get("/api/projects").json()
    if not any(p["name"] == "Simple Project" for p in projects):
        pytest.skip("bundled sample project not available")
    pid = next(p["id"] for p in projects if p["name"] == "Simple Project")
    job = client.post(f"/api/projects/{pid}/merge", json={}).json()["job_id"]
    j = _wait_job(client, job)
    assert j["status"] == "completed", j.get("log_tail", [])[-5:]
    return pid, j["result"]


def _dataset_without_uid():
    return pd.DataFrame([
        {"UT": "WOS:1", "DB": "ISI", "TI": "Scarcity and attention", "PY": 2016,
         "AU": "Smith J", "AB": "", "C1": "WoS address", "DI": ""},
        {"UT": "2-s2.0-1", "DB": "SCOPUS", "TI": "Scarcity and Attention", "PY": 2016,
         "AU": "Smith J.; Doe A.", "AB": "Scopus abstract", "C1": "Scopus address", "DI": ""},
        {"UT": "WOS:2", "DB": "ISI", "TI": "Unrelated", "PY": 2019,
         "AU": "Roe R", "AB": "x", "C1": "y", "DI": ""},
    ])


def _pair(pid="p1", **over):
    base = {"pair_id": pid, "confidence": 0.85, "wos_ut": "WOS:1", "scp_ut": "2-s2.0-1",
            "wos_doi": "", "scp_doi": "", "wos_title": "Scarcity and attention",
            "scp_title": "Scarcity and Attention", "wos_year": 2016, "scp_year": 2016}
    base.update(over)
    return base


def _uid_dataset():
    df = _dataset_without_uid()
    df.insert(0, "UID", [1, 2, 3])
    return df


# ── source UID assignment / pair merge (unit) ────────────────────────────

def test_source_frames_get_temporary_ids_that_never_collide():
    wos = pd.DataFrame({"TI": ["a", "b", "c"], "UID": [1, 2, 3]})     # a re-imported export's own keys
    scp = pd.DataFrame({"TI": ["a", "b"], "UID": [1, 2]}, index=[7, 7])   # repeated labels, same keys
    _assign_source_ids(wos, scp)
    assert list(wos["UID"]) == ["w0", "w1", "w2"] and list(scp["UID"]) == ["s0", "s1"]
    assert not set(wos["UID"]) & set(scp["UID"])


def test_source_frames_without_uid_column_get_temporary_ids():
    wos, scp = pd.DataFrame({"TI": ["a", "b"]}), pd.DataFrame({"TI": ["c"]})
    _assign_source_ids(wos, scp)
    assert list(wos["UID"]) == ["w0", "w1"] and list(scp["UID"]) == ["s0"]


def _stage_frames():
    """Pair (w0, s1) matched; w1, w2 and s0, s2 unmatched; (w1, s0) is a pending borderline pair."""
    wos = pd.DataFrame({"TI": ["m", "x", "y"], "UID": ["w0", "w1", "w2"]})
    scp = pd.DataFrame({"TI": ["x2", "m2", "z"], "UID": ["s0", "s1", "s2"]})
    matches = [{"pair_id": "p000001", "wos_tmp": "w0", "scp_tmp": "s1", "wos_index": 0, "scp_index": 1}]
    borderline = [{"pair_id": "p000002", "wos_uid": "w1", "scp_uid": "s0"}]
    wos_nm, scp_nm = wos.iloc[[1, 2]].copy(), scp.iloc[[0, 2]].copy()
    merged = pd.DataFrame({"TI": ["m"], "UID": ["w0"]})                # the pair keeps the WoS temp id
    final = pd.concat([merged, wos_nm, scp_nm], ignore_index=True)
    return final, matches, borderline, wos_nm, scp_nm


def test_final_keys_are_sequential_in_final_row_order_and_references_are_translated():
    final, matches, borderline, wos_nm, scp_nm = _stage_frames()
    assert _assign_final_keys(final, matches, borderline, wos_nm, scp_nm) == 5
    assert list(final["UID"]) == [1, 2, 3, 4, 5] and final["UID"].dtype == "int64"
    assert list(final["TI"]) == ["m", "x", "y", "x2", "z"]
    assert matches[0]["uid"] == 1                                       # audit: the merged row's key
    assert borderline[0]["wos_uid"] == 2 and borderline[0]["scp_uid"] == 4   # both rows live in the dataset
    assert list(wos_nm["UID"]) == [2, 3] and list(scp_nm["UID"]) == [4, 5]   # Lost_* files carry final keys
    assert wos_nm["UID"].dtype == "int64"


def test_final_keys_refuse_a_frame_that_is_not_in_the_expected_order():
    final, matches, borderline, wos_nm, scp_nm = _stage_frames()
    final = final.iloc[::-1].reset_index(drop=True)
    with pytest.raises(RuntimeError, match="merge_uid_mismatch"):
        _assign_final_keys(final, matches, borderline, wos_nm, scp_nm)


def test_final_keys_for_a_merge_without_matches():
    wos = pd.DataFrame({"TI": ["a"], "UID": ["w0"]})
    scp = pd.DataFrame({"TI": ["b"], "UID": ["s0"]})
    final = pd.concat([pd.DataFrame(), wos, scp], ignore_index=True)
    assert _assign_final_keys(final, [], [], wos.copy(), scp.copy()) == 2
    assert list(final["UID"]) == [1, 2]


def test_matched_pair_keeps_the_wos_uid_and_logs_no_uid_conflict():
    w = {"UID": "w3", "TI": "t", "AB": "", "TC": 3}
    s = {"UID": "s9", "TI": "t", "AB": "abstract", "TC": 4}
    merged, conflicts = merge_pair_with_preferences("p1", w, s, ["UID", "TI", "AB", "TC"])
    assert merged["UID"] == "w3"
    assert all(c["field"] != "UID" for c in conflicts)
    assert any(c["field"] == "TC" for c in conflicts)             # real conflicts still logged
    # An empty WoS id can only come from a corrupt frame; the Scopus id is then the fallback.
    merged, _ = merge_pair_with_preferences("p1", {**w, "UID": ""}, s, ["UID", "TI"])
    assert merged["UID"] == "s9"


# ── accepting a pair: UID first, no positional / lookalike mapping ───────

def test_pair_with_uids_merges_exactly_those_rows():
    df = _uid_dataset()
    pair = _pair(wos_uid=1, scp_uid=2)
    out, outcomes, _ = _merge_accepted_pairs(df, [pair], set())
    assert outcomes == {"p1": "merged"}
    assert list(out["UID"]) == [1, 3]                                  # the pair keeps the WoS key; 2 is retired
    assert out.loc[out["UID"] == 1, "AB"].iloc[0] == "Scopus abstract"


def test_pair_uids_match_however_the_queue_spells_them():
    # An Excel round trip can hand the keys back as floats, numeric text or numpy ints.
    import numpy as np
    for w, s_ in ((1.0, 2.0), ("1", "2"), (np.int64(1), np.int64(2)), (" 1 ", "2.0")):
        out, outcomes, _ = _merge_accepted_pairs(_uid_dataset(), [_pair(wos_uid=w, scp_uid=s_)], set())
        assert outcomes == {"p1": "merged"} and list(out["UID"]) == [1, 3], (w, s_)


def test_uid_lookup_survives_row_reordering_and_deletions():
    df = _uid_dataset().iloc[::-1].reset_index(drop=True)           # positions all changed
    df = pd.concat([df.iloc[:0], df], ignore_index=True)
    pair = _pair(wos_uid=1, scp_uid=2)
    out, outcomes, _ = _merge_accepted_pairs(df, [pair], set())
    assert outcomes == {"p1": "merged"} and len(out) == 2
    assert "2-s2.0-1" not in set(out["UT"])


def test_missing_uid_is_reported_not_mapped_to_a_lookalike():
    # The pair's WoS UID is gone, but another WoS row carries the same UT/title: the old
    # identifier fallback would have merged into it. With UIDs in the queue it must not.
    df = _uid_dataset()
    df.loc[0, "UID"] = 999                                          # a different key now holds WOS:1
    pair = _pair(wos_uid=1, scp_uid=2)
    out, outcomes, _ = _merge_accepted_pairs(df, [pair], set())
    assert outcomes == {"p1": "unresolved:wos_missing"}
    assert len(out) == 3 and "2-s2.0-1" in set(out["UT"])
    # Scopus side gone -> the decision is effectively applied; nothing else is touched.
    df = _uid_dataset()
    pair = _pair(wos_uid=1, scp_uid=555)
    out, outcomes, _ = _merge_accepted_pairs(df, [pair], set())
    assert outcomes == {"p1": "noop_scp_missing"} and len(out) == 3


def test_queue_without_uid_columns_falls_back_to_identifiers():
    out, outcomes, _ = _merge_accepted_pairs(_uid_dataset(), [_pair()], set())   # no wos_uid/scp_uid
    assert outcomes == {"p1": "merged"} and len(out) == 2
    # NaN cells (older queue read back from xlsx next to new columns) also mean "no UID".
    out, outcomes, _ = _merge_accepted_pairs(
        _uid_dataset(), [_pair(wos_uid=float("nan"), scp_uid=float("nan"))], set())
    assert outcomes == {"p1": "merged"}


# ── decide_borderline on a dataset without UIDs ──────────────────────────

def test_decide_borderline_persists_uids_before_the_snapshot(sample_client):
    from services import analyses, dataset_io, smart_merger

    pid = sample_client.post("/api/projects", json={"name": "NoUid"}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "smart")
    analyses.set_active_analysis(pid, aid)
    dataset_io.atomic_write_dataset(_dataset_without_uid(), adir / "merged.parquet")
    q = _pair()
    q.update({"wos_index": 0, "scp_index": 1, "jw_title": 0.9, "year_diff": 0, "surname_match": True,
              "doi_variant": False, "stage_label": "Borderline", "reason": "x",
              "wos_surname": "SMITH", "scp_surname": "SMITH", "wos_journal": "", "scp_journal": "",
              "wos_volume": "", "scp_volume": ""})
    pd.DataFrame([q]).to_excel(adir / "borderline_queue.xlsx", index=False)

    res = smart_merger.decide_borderline(pid, [{"pair_id": "p1", "decision": "accept"}])
    assert res["merged_pairs"] == 1 and res["snapshot"]
    after = dataset_io.read_dataset(adir / "merged.parquet")
    assert list(after["UID"]) == [1, 3] and after["UID"].dtype == "int64"   # keys 1..3 were issued first
    snap = dataset_io.read_dataset(Path(smart_merger.storage.settings.storage_path) / res["snapshot"])
    assert list(snap["UID"]) == [1, 2, 3]                              # snapshot carries the same UIDs
    assert analyses.get_uid_max(pid, aid) == 3                         # the high-water mark was recorded
    # list_borderline tolerates a queue without uid columns
    items = smart_merger.list_borderline(pid)
    assert items[0]["wos_uid"] is None and items[0]["scp_uid"] is None


def test_decide_borderline_uid_persist_failure_is_a_typed_409_and_touches_nothing(sample_client, monkeypatch):
    from fastapi import HTTPException
    from services import analyses, dataset_io, smart_merger

    pid = sample_client.post("/api/projects", json={"name": "NoUidFail"}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "smart")
    analyses.set_active_analysis(pid, aid)
    dataset_io.atomic_write_dataset(_dataset_without_uid(), adir / "merged.parquet")
    q = _pair()
    q.update({"wos_index": 0, "scp_index": 1, "jw_title": 0.9, "year_diff": 0, "surname_match": True,
              "doi_variant": False, "stage_label": "Borderline", "reason": "x"})
    pd.DataFrame([q]).to_excel(adir / "borderline_queue.xlsx", index=False)

    def boom(df, path):
        raise OSError("disk full")

    with monkeypatch.context() as m:
        m.setattr(dataset_io, "atomic_write_dataset", boom)
        with pytest.raises(HTTPException) as exc:
            smart_merger.decide_borderline(pid, [{"pair_id": "p1", "decision": "accept"}])
    assert exc.value.status_code == 409 and exc.value.detail == "uid_persist_failed"
    assert not (adir / "borderline_state.json").exists()               # no decision state was written
    assert "UID" not in dataset_io.read_dataset(adir / "merged.parquet").columns


# ── Smart Merge on the bundled sample ────────────────────────────────────

def test_sample_merge_result_is_unchanged_and_every_row_has_a_uid(sample_client):
    from services import analyses, dataset_io, smart_merger

    pid, result = _merged_sample(sample_client)
    assert result["merged_count"] == 3735
    assert result["matched_pairs"] == 422
    assert result["borderline_count"] == 20

    path = analyses.active_dataset_path(pid)
    adir = path.parent
    df = dataset_io.read_dataset(path)
    assert len(df) == 3735
    # Sequential keys 1..N in the final row order, like a database primary key.
    assert df["UID"].dtype == "int64" and list(df["UID"]) == list(range(1, 3736))
    meta = analyses.get_analysis_meta(pid, adir.name)
    assert meta["uid_scheme"] == "seq_v1" and meta["uid_max"] == 3735

    # Final order: merged pairs first, then the unmatched WoS rows, then the unmatched Scopus rows.
    assert (df["DB"].iloc[:422] == "BIBEXPY_SMART").all()
    assert (df["DB"].iloc[422:] != "BIBEXPY_SMART").all()

    # match_audit: one `uid` per pair = the key of the merged row; no temporary ids persisted.
    audit = pd.read_excel(adir / "match_audit.xlsx")
    assert len(audit) == 422 and "uid" in audit.columns
    assert "wos_uid" not in audit.columns and "scp_uid" not in audit.columns
    assert sorted(audit["uid"]) == list(range(1, 423))
    smart_rows = set(df.loc[df["DB"] == "BIBEXPY_SMART", "UID"])
    assert smart_rows == set(audit["uid"])

    # The queue names both rows by their final keys, and both are in the dataset.
    final = set(df["UID"])
    queue = pd.read_excel(adir / "borderline_queue.xlsx")
    assert len(queue) == 20 and {"wos_uid", "scp_uid"} <= set(queue.columns)
    assert queue["wos_uid"].isin(final).all() and queue["scp_uid"].isin(final).all()
    assert (queue["wos_uid"] != queue["scp_uid"]).all()
    for _, row in queue.iterrows():                                    # the keys point at the right rows
        w = df.loc[df["UID"] == row["wos_uid"]].iloc[0]
        s_ = df.loc[df["UID"] == row["scp_uid"]].iloc[0]
        if w["DB"] == "ISI":                                           # (a row matched elsewhere is a merged row)
            assert str(w["TI"]).strip()[:200] == str(row["wos_title"]).strip()
        if s_["DB"] == "SCOPUS":
            assert str(s_["TI"]).strip()[:200] == str(row["scp_title"]).strip()
    items = smart_merger.list_borderline(pid)
    assert items and all(int(it["wos_uid"]) in final and int(it["scp_uid"]) in final for it in items)

    # Lost_* sheets (unmatched rows) carry the final keys of the same rows.
    lost = pd.read_excel(adir / "Lost_Wos_Records.xlsx")
    assert lost["UID"].isin(final).all() and lost["UID"].min() == 423
    by_key = df.set_index("UID")["UT"]
    assert (lost["UT"].astype(str).values == by_key.loc[lost["UID"]].astype(str).values).all()
    lost_scp = pd.read_excel(adir / "Lost_Scopus_Records.xlsx")
    assert lost_scp["UID"].isin(final).all() and lost_scp["UID"].max() == 3735


def test_accept_after_deleting_unrelated_rows_and_restoring_merges_exactly_two_uids(sample_client):
    from services import analyses, dataset_io

    client = sample_client
    pid, _ = _merged_sample(client)
    path = analyses.active_dataset_path(pid)
    original = dataset_io.read_dataset(path)
    base_uids = list(original["UID"].astype(str))

    from services import smart_merger
    items = smart_merger.list_borderline(pid)
    first = items[0]
    # Borderline pairs can share a record; take a second pair that shares none with the first.
    second = next(it for it in items[1:]
                  if not {it["wos_uid"], it["scp_uid"]} & {first["wos_uid"], first["scp_uid"]})
    pair_rows = {first["wos_uid"], first["scp_uid"], second["wos_uid"], second["scp_uid"]}
    assert len(pair_rows) == 4

    # 1) Delete rows that come BEFORE the pair rows -> every position shifts.
    victims = [u for u in base_uids[:12] if u not in pair_rows][:5]
    r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": victims, "reason": "t"})
    assert r.status_code == 200 and r.json()["deleted"] == 5
    delete_snapshot = r.json()["snapshot"]

    r = client.post(f"/api/projects/{pid}/merge/borderline/decide",
                    json={"decisions": [{"pair_id": first["pair_id"], "decision": "accept"}]})
    assert r.status_code == 200 and r.json()["merged_pairs"] == 1
    now = list(dataset_io.read_dataset(path)["UID"].astype(str))
    assert set(now) == set(base_uids) - set(victims) - {first["scp_uid"]}
    assert first["wos_uid"] in now and len(now) == len(base_uids) - 5 - 1

    # 2) Restore the pre-delete snapshot (positions are back), then accept the other pair.
    r = client.post(f"/api/projects/{pid}/records/restore-snapshot", json={"snapshot": delete_snapshot})
    assert r.status_code == 200
    assert list(dataset_io.read_dataset(path)["UID"].astype(str)) == base_uids
    r = client.post(f"/api/projects/{pid}/merge/borderline/decide",
                    json={"decisions": [{"pair_id": second["pair_id"], "decision": "accept"}]})
    assert r.status_code == 200 and r.json()["merged_pairs"] == 1
    now = set(dataset_io.read_dataset(path)["UID"].astype(str))
    assert now == set(base_uids) - {second["scp_uid"]}              # exactly one UID retired


# ── enrichment ───────────────────────────────────────────────────────────

class _Ctx:
    cancelled = False

    def __init__(self):
        self.messages = []

    def log(self, msg, key=None, params=None):
        self.messages.append(msg)

    def progress(self, value):
        pass


def test_uid_of_never_invents_a_positional_label():
    from services import enricher
    df = pd.DataFrame({"UID": pd.Series([7, "", None], dtype=object), "TI": ["x", "y", "z"]})
    assert enricher._uid_of(df, 0) == "7"
    assert enricher._uid_of(pd.DataFrame({"UID": [12]}), 0) == "12"
    for idx in (1, 2):
        with pytest.raises(RuntimeError, match="^uid_missing"):
            enricher._uid_of(df, idx)
    with pytest.raises(RuntimeError, match="^uid_missing"):
        enricher._uid_of(pd.DataFrame({"TI": ["x"]}), 0)


def test_fill_all_on_a_uid_less_dataset_persists_uids_and_the_provenance_uses_them(tmp_path, monkeypatch):
    from bibex_core.modules import api_utils
    from services import dataset_io, enricher

    src = tmp_path / "A1" / "merged.parquet"
    src.parent.mkdir()
    df = pd.DataFrame({"TI": ["Alpha beta gamma study", "Delta epsilon zeta study"],
                       "DI": ["", ""], "PY": ["2020", "2021"], "AU": ["Smith J", "Roe R"]})
    dataset_io.atomic_write_dataset(df, src)
    assert "UID" not in dataset_io.read_dataset(src).columns

    seen = {}

    def fake_snapshot(pid, d, tag, base=None):
        seen["on_disk_at_snapshot"] = list(dataset_io.read_dataset(src)["UID"])   # persisted BEFORE the snapshot
        seen["in_snapshot"] = list(d["UID"])
        return f"snapshots/{tag}.parquet"

    async def no_api_pass(ctx, d, **kw):
        return {"total": 0, "enriched": 0}

    async def no_addr_pass(ctx, d, email, **kw):
        return {"rows": 0, "addr": 0, "dois": 0}

    monkeypatch.setattr(enricher, "_active_path", lambda pid: src)
    monkeypatch.setattr(enricher.analyses, "get_active_analysis_id", lambda pid: "A1")
    monkeypatch.setattr(enricher, "_snapshot", fake_snapshot)
    monkeypatch.setattr(enricher.storage, "touch_project", lambda pid: None)
    monkeypatch.setattr(enricher, "_api_pass", no_api_pass)
    monkeypatch.setattr(enricher, "_complete_addresses_pass", no_addr_pass)
    monkeypatch.setattr(api_utils, "lookup_doi",
                        lambda rec, **kw: api_utils.DoiDecision(None, "no_match", reason="stub"))

    result = asyncio.run(enricher.run_fill_all(_Ctx(), "p1"))
    final_uids = list(dataset_io.read_dataset(src)["UID"])
    assert final_uids == [1, 2]
    assert seen["on_disk_at_snapshot"] == final_uids == seen["in_snapshot"]

    csv_path = next(src.parent.glob("doi_lookup_*.csv"))
    prov = pd.read_csv(csv_path, encoding="utf-8-sig")
    assert list(prov["UID"]) == final_uids
    assert not prov["UID"].astype(str).str.startswith("row").any()
    assert result["doi"]["scanned"] == 2


def test_api_enrichment_persist_failure_is_not_swallowed(tmp_path, monkeypatch):
    from services import dataset_io, enricher

    src = tmp_path / "A1" / "merged.parquet"
    src.parent.mkdir()
    dataset_io.atomic_write_dataset(pd.DataFrame({"TI": ["x"], "DI": ["10.1/x"]}), src)

    def boom(df, path):
        raise OSError("disk full")

    monkeypatch.setattr(enricher, "_active_path", lambda pid: src)
    monkeypatch.setattr(dataset_io, "atomic_write_dataset", boom)
    with pytest.raises(RuntimeError, match="^uid_persist_failed"):
        asyncio.run(enricher.run_api_enrichment(_Ctx(), "p1"))
