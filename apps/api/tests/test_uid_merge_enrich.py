"""UID everywhere: Smart Merge, borderline review and enrichment.

Pins the identity rules of package U3:

* WoS and Scopus source rows get a UID right after loading; a matched pair keeps the
  WoS row's UID, an unmatched row keeps its own, and the merge result is unchanged
  (bundled sample: 3735 records, 422 matched pairs, 20 borderline pairs).
* match_audit.xlsx / borderline_queue.xlsx name the rows by UID.
* Accepting a borderline pair locates the rows by UID first, never by position, and a
  UID that is no longer in the dataset is reported instead of mapped to another row.
* The enrichment jobs persist UIDs before their snapshot and never write 'row N' labels.
"""

import asyncio
import re
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
    _assign_source_uids,
    _merge_accepted_pairs,
    merge_pair_with_preferences,
)

_SAMPLE = _API_ROOT.parents[1] / "python_pkg" / "src" / "bibexpy" / "_samples" / "simple_project"
_UID_RE = re.compile(r"^r_[0-9a-f]{16}$")


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
    df.insert(0, "UID", ["r_" + f"{i:016x}" for i in (1, 2, 3)])
    return df


# ── source UID assignment / pair merge (unit) ────────────────────────────

def test_source_frames_get_unique_uids_and_keep_valid_ones():
    wos = pd.DataFrame({"TI": ["a", "b", "c"]})
    scp = pd.DataFrame({"TI": ["a", "b"], "UID": ["r_" + "0" * 16, "r_" + "1" * 16]})
    wos["UID"] = ["r_" + "0" * 16, "", "r_" + "2" * 16]          # clashes with scp[0], one blank
    _assign_source_uids(wos, scp)
    allu = list(wos["UID"]) + list(scp["UID"])
    assert len(set(allu)) == 5 and all(_UID_RE.match(u) for u in allu)
    assert wos["UID"].iloc[0] == "r_" + "0" * 16 and wos["UID"].iloc[2] == "r_" + "2" * 16
    assert scp["UID"].iloc[0] != "r_" + "0" * 16                  # the Scopus repeat was renewed
    assert scp["UID"].iloc[1] == "r_" + "1" * 16                  # valid unique UID untouched


def test_scopus_uid_clash_is_renewed_by_position_even_with_repeated_index_labels():
    wos = pd.DataFrame({"TI": ["a"], "UID": ["r_" + "0" * 16]})
    scp = pd.DataFrame({"TI": ["b", "c", "d"],
                        "UID": ["r_" + "0" * 16, "r_" + "1" * 16, "r_" + "0" * 16]},
                       index=[7, 7, 7])                                # repeated labels
    _assign_source_uids(wos, scp)
    allu = list(wos["UID"]) + list(scp["UID"])
    assert len(set(allu)) == 4 and all(_UID_RE.match(u) for u in allu)
    assert scp["UID"].iloc[1] == "r_" + "1" * 16                      # the valid unique one is untouched


def test_source_frames_without_uid_column_get_one():
    wos, scp = pd.DataFrame({"TI": ["a", "b"]}), pd.DataFrame({"TI": ["c"]})
    _assign_source_uids(wos, scp)
    allu = list(wos["UID"]) + list(scp["UID"])
    assert len(set(allu)) == 3 and all(_UID_RE.match(u) for u in allu)


def test_matched_pair_keeps_the_wos_uid_and_logs_no_uid_conflict():
    w = {"UID": "r_w", "TI": "t", "AB": "", "TC": 3}
    s = {"UID": "r_s", "TI": "t", "AB": "abstract", "TC": 4}
    merged, conflicts = merge_pair_with_preferences("p1", w, s, ["UID", "TI", "AB", "TC"])
    assert merged["UID"] == "r_w"
    assert all(c["field"] != "UID" for c in conflicts)
    assert any(c["field"] == "TC" for c in conflicts)             # real conflicts still logged
    # An empty WoS UID can only come from a corrupt frame; the Scopus UID is then the fallback.
    merged, _ = merge_pair_with_preferences("p1", {**w, "UID": ""}, s, ["UID", "TI"])
    assert merged["UID"] == "r_s"


# ── accepting a pair: UID first, no positional / lookalike mapping ───────

def test_pair_with_uids_merges_exactly_those_rows():
    df = _uid_dataset()
    pair = _pair(wos_uid="r_" + f"{1:016x}", scp_uid="r_" + f"{2:016x}")
    out, outcomes, _ = _merge_accepted_pairs(df, [pair], set())
    assert outcomes == {"p1": "merged"}
    assert list(out["UID"]) == ["r_" + f"{1:016x}", "r_" + f"{3:016x}"]
    assert out.loc[out["UID"] == "r_" + f"{1:016x}", "AB"].iloc[0] == "Scopus abstract"


def test_uid_lookup_survives_row_reordering_and_deletions():
    df = _uid_dataset().iloc[::-1].reset_index(drop=True)           # positions all changed
    df = pd.concat([df.iloc[:0], df], ignore_index=True)
    pair = _pair(wos_uid="r_" + f"{1:016x}", scp_uid="r_" + f"{2:016x}")
    out, outcomes, _ = _merge_accepted_pairs(df, [pair], set())
    assert outcomes == {"p1": "merged"} and len(out) == 2
    assert "2-s2.0-1" not in set(out["UT"])


def test_missing_uid_is_reported_not_mapped_to_a_lookalike():
    # The pair's WoS UID is gone, but another WoS row carries the same UT/title: the old
    # identifier fallback would have merged into it. With UIDs in the queue it must not.
    df = _uid_dataset()
    df.loc[0, "UID"] = "r_" + "f" * 16                              # a different row now holds WOS:1
    pair = _pair(wos_uid="r_" + f"{1:016x}", scp_uid="r_" + f"{2:016x}")
    out, outcomes, _ = _merge_accepted_pairs(df, [pair], set())
    assert outcomes == {"p1": "unresolved:wos_missing"}
    assert len(out) == 3 and "2-s2.0-1" in set(out["UT"])
    # Scopus side gone -> the decision is effectively applied; nothing else is touched.
    df = _uid_dataset()
    pair = _pair(wos_uid="r_" + f"{1:016x}", scp_uid="r_" + "e" * 16)
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
    assert len(after) == 2 and after["UID"].is_unique and all(_UID_RE.match(u) for u in after["UID"])
    snap = dataset_io.read_dataset(Path(smart_merger.storage.settings.storage_path) / res["snapshot"])
    assert len(snap) == 3 and snap["UID"].is_unique                    # snapshot carries the same UIDs
    assert set(after["UID"]) <= set(snap["UID"])
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
    uids = df["UID"].astype(str)
    assert uids.is_unique and all(_UID_RE.match(u) for u in uids)

    # Matched rows keep the WoS UID; the Scopus UID is retired.
    audit = pd.read_excel(adir / "match_audit.xlsx")
    assert len(audit) == 422 and {"wos_uid", "scp_uid"} <= set(audit.columns)
    final = set(uids)
    assert audit["wos_uid"].astype(str).isin(final).all()
    assert not audit["scp_uid"].astype(str).isin(final).any()
    smart_rows = set(df.loc[df["DB"] == "BIBEXPY_SMART", "UID"].astype(str))
    assert smart_rows == set(audit["wos_uid"].astype(str))

    # The queue names both rows by UID, and both are in the dataset (unmatched rows keep their own).
    queue = pd.read_excel(adir / "borderline_queue.xlsx")
    assert len(queue) == 20 and {"wos_uid", "scp_uid"} <= set(queue.columns)
    assert queue["wos_uid"].astype(str).isin(final).all() and queue["scp_uid"].astype(str).isin(final).all()
    assert (queue["wos_uid"] != queue["scp_uid"]).all()
    items = smart_merger.list_borderline(pid)
    assert items and all(it["wos_uid"] in final and it["scp_uid"] in final for it in items)

    # Lost_* sheets (unmatched rows) carry the UIDs that live on in the dataset.
    lost = pd.read_excel(adir / "Lost_Wos_Records.xlsx")
    assert lost["UID"].astype(str).isin(final).all()


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
    df = pd.DataFrame({"UID": ["r_a", "", None], "TI": ["x", "y", "z"]})
    assert enricher._uid_of(df, 0) == "r_a"
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
    assert len(set(final_uids)) == 2 and all(_UID_RE.match(u) for u in final_uids)
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
