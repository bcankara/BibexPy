"""UID everywhere - disambiguation (sequential integer keys).

Disambiguation proposals are stored and applied in a LATER request, after rows may have
been deleted, filtered or restored. These tests pin that every row reference inside a
proposal is the row UID (never a position; a sequential integer key written as canonical
text), that applying resolves the UIDs in the CURRENT dataset (a vanished UID is skipped
and counted, never mapped to another row), that repairs issue keys above the analysis
high-water mark (never reusing one), and that proposal files from before the sequential
keys / from another analysis are refused.
"""

import asyncio
import json
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models")):
            sys.modules.pop(mod, None)
    from main import app
    return TestClient(app)


class _Ctx:
    cancelled = False

    def __init__(self):
        self.lines: list[str] = []

    def log(self, line, **_kw):
        self.lines.append(line)

    def progress(self, _v):
        pass


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No network, no LLM: the ORCID lookup and the LLM client are switched off for every test."""
    from jobs.runner import JobRunner
    monkeypatch.setattr(JobRunner, "_write_audit", lambda self, job: None)


def _no_llm(monkeypatch, pipeline):
    def _raise():
        raise HTTPException(400, "llm_key_missing")

    monkeypatch.setattr(pipeline, "_client", _raise)
    monkeypatch.setattr(pipeline.settings, "disambiguation_orcid_lookup", False)
    monkeypatch.setattr(pipeline.settings, "disambiguation_orcid_persist", False)


# Row 0 and 1 come BEFORE the target rows and are removed later (delete + filter), which
# shifts the position of every target row by two.
_ROWS = [
    # AU, PY, WC
    ("ALPHA X; AAA P", 2005, "Chemistry"),         # 0  deleted by UID
    ("BETA Y; BBB Q", 1990, "Chemistry"),          # 1  removed by the year filter
    ("SMITH J; DOE W", 2020, "Physics"),           # 2  \
    ("SMITH J; ROE B", 2021, "Physics"),           # 3   > plain group (largest)
    ("SMITH J; COE C", 2022, "Physics"),           # 4  /
    ("SMITH J; FOE D", 2020, "Medicine"),          # 5  \  (b) group
    ("SMITH J; GOE E", 2021, "Medicine"),          # 6  /
    ("SMITH J; ZED Q", 2021, ""),                  # 7  same name, NO field data: not in any group
    ("OTHER Z; CCC R", 2020, "Physics"),           # 8
]


# Keys of a dataset that already carries a UID column: deliberately far from the row positions.
_KEY0 = 100


def _make_project(client, rows=_ROWS, with_uid=False):
    from services import analyses, dataset_io as dio

    pid = client.post("/api/projects", json={"name": "UID disambiguation"}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "smart")
    df = pd.DataFrame({
        "AU": [r[0] for r in rows],
        "PY": [r[1] for r in rows],
        "WC": [r[2] for r in rows],
        "TI": [f"Title {i}" for i in range(len(rows))],
    })
    if with_uid:
        df.insert(0, "UID", pd.Series([_KEY0 + i for i in range(len(rows))], dtype="int64"))
    dio.atomic_write_dataset(df, adir / "merged.parquet")
    analyses.finalize_analysis(pid, aid)
    return pid, aid, adir


def _scan_authors(pid, monkeypatch):
    from services.disambiguation import pipeline

    _no_llm(monkeypatch, pipeline)
    return run(pipeline.run_author_disambiguation(_Ctx(), pid, "auto"))


def _proposals(adir):
    return json.loads((adir / "disambiguation_authors.json").read_text(encoding="utf-8"))


def _dataset(adir):
    from services import dataset_io as dio
    return dio.read_dataset(adir / "merged.parquet")


def dio_key(value):
    from services import dataset_io as dio
    return dio.uid_key(value)


def _keys(df, col="TI"):
    """title -> canonical UID text, as a request / proposal file carries it."""
    return {t: dio_key(u) for t, u in zip(df[col], df["UID"])}


def _split_for(adir, name="SMITH J"):
    splits = [s for s in _proposals(adir)["splits"] if s["name"] == name]
    assert len(splits) == 1, splits
    return splits[0]


# ── scan output: UIDs, schema, persistence ───────────────────────────────

def test_scan_persists_uids_and_emits_uid_records(client, monkeypatch):
    pid, aid, adir = _make_project(client)             # the dataset has NO UID column
    assert "UID" not in _dataset(adir).columns
    result = _scan_authors(pid, monkeypatch)
    assert result["splits_proposed"] == 1

    df = _dataset(adir)
    assert "UID" in df.columns and df["UID"].is_unique and not df["UID"].isna().any()
    # a dataset born without keys gets the sequential 1..N, as an int64 column
    assert str(df["UID"].dtype) == "int64" and list(df["UID"]) == list(range(1, len(_ROWS) + 1))
    uid_of_ti = _keys(df)

    props = _proposals(adir)
    assert props["schema"] == "uid_v2" and props["analysis_id"] == aid
    split = _split_for(adir)
    groups = {g["suffix"]: g for g in split["groups"]}
    assert set(groups) == {"", "(b)"}
    assert all(isinstance(r, str) for g in split["groups"] for r in g["records"])
    assert set(groups[""]["records"]) == {uid_of_ti[f"Title {i}"] for i in (2, 3, 4)}
    assert set(groups["(b)"]["records"]) == {uid_of_ti[f"Title {i}"] for i in (5, 6)}


def test_scan_keeps_existing_uids(client, monkeypatch):
    pid, aid, adir = _make_project(client, with_uid=True)
    _scan_authors(pid, monkeypatch)
    assert list(_dataset(adir)["UID"]) == [_KEY0 + i for i in range(len(_ROWS))]
    groups = {g["suffix"]: g for g in _split_for(adir)["groups"]}
    assert set(groups["(b)"]["records"]) == {str(_KEY0 + 5), str(_KEY0 + 6)}


def test_scan_repairs_above_the_high_water_mark_and_never_reuses_a_key(client, monkeypatch):
    """A row without a key gets uid_max+1.. - not max-in-frame+1 - so a key that once existed
    (and was deleted) is never issued again. The new uid_max is persisted."""
    from services import analyses

    pid, aid, adir = _make_project(client, with_uid=True)
    analyses.bump_uid_max(pid, aid, 500)                      # keys up to 500 were issued once
    df = _dataset(adir)
    uid = df["UID"].astype("float64")
    uid.iloc[3] = float("nan")                                # one row lost its key
    df["UID"] = uid
    df.to_parquet(adir / "merged.parquet", index=False)       # bypass the writer's check

    _scan_authors(pid, monkeypatch)
    after = _dataset(adir)
    assert str(after["UID"].dtype) == "int64" and after["UID"].is_unique
    assert after["UID"].iloc[3] == 501
    assert [int(x) for x in after["UID"].iloc[[0, 1, 2, 4]]] == [_KEY0, _KEY0 + 1, _KEY0 + 2, _KEY0 + 4]
    assert analyses.get_uid_max(pid, aid) == 501


def test_all_scan_kinds_write_the_uid_schema_and_uid_records(client, monkeypatch):
    from services.disambiguation import pipeline

    rows = [
        ("A; B", 2020, "x"), ("C; D", 2021, "x"), ("E; F", 2022, "x"),
    ]
    pid, aid, adir = _make_project(client, rows=rows)
    df = _dataset(adir)
    df["C1"] = ["[A] Univ Ankara, Ankara, Turkey", "[B] Ankara Univ, Ankara, Turkiye", "[C] Univ Istanbul, Istanbul, Turkey"]
    from services import dataset_io as dio
    dio.atomic_write_dataset(df, adir / "merged.parquet")
    _no_llm(monkeypatch, pipeline)

    run(pipeline.run_country_standardization(_Ctx(), pid))
    run(pipeline.run_org_rollup(_Ctx(), pid))
    for kind in ("countries", "organizations"):
        props = json.loads((adir / f"disambiguation_{kind}.json").read_text(encoding="utf-8"))
        assert props["schema"] == "uid_v2" and props["analysis_id"] == aid
        uids = {dio_key(u) for u in _dataset(adir)["UID"]}
        for c in props["clusters"]:
            for m in c["members"]:
                assert all(r in uids for r in m.get("records", []))


# ── blocking.py: order and truncation semantics are preserved ────────────

def test_blocking_records_are_uids_sorted_by_position_and_truncated_before_mapping():
    from services.disambiguation import blocking

    n = 120
    df = pd.DataFrame({
        "UID": [n - i for i in range(n)],                    # UID order is the REVERSE of position order
        "AU": ["SMITH JOHN" if i % 2 == 0 else "SMITH JON" for i in range(n)],
        "PY": [2020] * n,
    })
    blocks = blocking.build_author_blocks(df, sim_threshold=0.5)
    cands = [c for b in blocks for c in (b.get("auto", []) + b.get("members", []))]
    assert len(cands) == 1
    recs = cands[0]["records"]
    assert len(recs) == 50 and all(isinstance(r, str) for r in recs)
    # the first 50 POSITIONS were kept (then mapped), in position order
    assert recs == [str(n - i) for i in range(50)]


def test_blocking_emits_the_same_text_for_int_float_and_string_keys():
    from services.disambiguation import blocking

    base = {"AU": ["SMITH JOHN", "SMITH JON", "SMITH JOHN"], "PY": [2020, 2021, 2022]}
    seen = []
    for keys in ([7, 8, 9], [7.0, 8.0, 9.0], ["7", " 8 ", "9.0"]):
        df = pd.DataFrame({"UID": keys, **base})
        recs = [r for s in blocking.build_author_splits(df) for g in s["groups"] for r in g["records"]]
        recs += [r for b in blocking.build_author_blocks(df, sim_threshold=0.5)
                 for c in b.get("auto", []) + b.get("members", []) for r in c["records"]]
        seen.append(recs)
        assert all(isinstance(r, str) and r in {"7", "8", "9"} for r in recs)
    assert seen[0] == seen[1] == seen[2] and seen[0]


def test_blocking_refuses_a_frame_without_uids():
    from services.disambiguation import blocking

    df = pd.DataFrame({"AU": ["SMITH J", "SMITH JON"], "PY": [2020, 2021]})
    with pytest.raises(ValueError, match="uid_invariant_violation"):
        blocking.build_author_blocks(df)
    with pytest.raises(ValueError, match="uid_invariant_violation"):
        blocking.build_author_splits(df)


def test_every_blocking_builder_emits_uid_strings():
    from services.disambiguation import blocking

    n = 6
    df = pd.DataFrame({
        "UID": [i + 1 for i in range(n)],
        "AU": ["SMITH J", "SMITH JOHN"] * 3,
        "C1": [
            "[A] Univ Ankara, Ankara, Turkey", "[A] Ankara Univ, Ankara, Turkiye",
            "[A] Univ Ankara, Ankara, USA", "[A] Univ Ankara, Ankara, United States",
            "[A] Univ Istanbul, Istanbul, Turkey", "[A] Istanbul Univ, Istanbul, Turkey",
        ],
        "WC": ["a", "b", "a", "b", "a", "b"],
        "PY": [2020] * n,
    })
    seen = []
    for builder in (blocking.build_author_blocks, blocking.build_affiliation_blocks,
                    blocking.build_country_blocks, blocking.build_org_rollup):
        for b in builder(df, sim_threshold=0.5):
            for c in b.get("auto", []) + b.get("members", []):
                seen.extend(c.get("records", []))
    for s in blocking.build_author_splits(df):
        for g in s["groups"]:
            seen.extend(g["records"])
    assert seen and all(isinstance(r, str) and r.isdigit() and 1 <= int(r) <= n for r in seen)


# ── orcid.py: UID lookups ─────────────────────────────────────────────────

def test_orcid_lookup_resolves_uids_not_positions():
    from services.disambiguation import orcid

    df = pd.DataFrame({
        "UID": [1, 2, 3],
        "OI": ["Smith, J/0000-0001-0000-0001", "Smith, J/0000-0001-0000-0002", ""],
    })
    cand = {"name_variants": ["SMITH J"], "records": ["2"]}
    umap = orcid.uid_label_map(df)
    assert orcid.orcids_for_candidate(df, cand, email=None, allow_api=False, uid_map=umap) == {"0000-0001-0000-0002"}
    # an unknown UID (and position 0, which is no key) is skipped, never mapped to another row
    cand = {"name_variants": ["SMITH J"], "records": ["9", 0, "3"]}
    assert orcid.orcids_for_candidate(df, cand, email=None, allow_api=False, uid_map=umap) == set()
    # the key compares by its canonical text: "2", 2, 2.0 and " 2 " are the same row
    for rec in (2, 2.0, " 2 "):
        assert orcid.orcids_for_candidate(df, {"name_variants": ["SMITH J"], "records": [rec]},
                                          email=None, allow_api=False, uid_map=umap) == {"0000-0001-0000-0002"}
    # the map is also built on demand
    assert orcid.orcids_for_candidate(df, {"name_variants": ["SMITH J"], "records": ["1"]},
                                      email=None, allow_api=False) == {"0000-0001-0000-0001"}


def test_enrich_rows_from_doi_takes_uids(monkeypatch):
    from services.disambiguation import orcid

    monkeypatch.setattr(orcid, "fetch_orcids_for_doi",
                        lambda doi, email=None: (("Smith J", f"orcid-{doi}", "", "TR"),))
    # row labels deliberately differ from positions and UIDs
    df = pd.DataFrame({"UID": [1, 2, 3], "DI": ["d0", "d1", "d2"]}, index=[10, 20, 30])
    out = orcid.enrich_rows_from_doi(df, ["2", "999", 2.0, 0], email=None)
    assert out["rows_filled"] == 1
    assert df.loc[20, "OI"] == "Smith J/orcid-d1"
    assert df.loc[[10, 30], "OI"].isna().all()


def test_apply_filled_cells_aligns_by_uid_not_position():
    from services.disambiguation import pipeline

    full = pd.DataFrame({"UID": [1, 2, 3, 4], "OI": ["", "", "keep", ""]})
    # the scan-time slice is in a DIFFERENT order than `full`, its keys arrive as floats, and
    # key 99 does not exist in `full`
    part = pd.DataFrame({"UID": [3.0, 2.0, 99.0], "OI": ["overwrite", "new-b", "new-x"]})
    out = pipeline._apply_filled_cells(full, part)
    assert list(out["OI"]) == ["", "new-b", "keep", ""]


# ── apply_splits: THE USER'S CASE ─────────────────────────────────────────

def _apply(client, pid, splits, **extra):
    return client.post(f"/api/projects/{pid}/disambiguate/apply",
                       json={"kind": "authors", "approved_splits": splits, **extra})


def test_split_lands_on_the_intended_records_after_delete_and_filter(client, monkeypatch):
    pid, aid, adir = _make_project(client)
    _scan_authors(pid, monkeypatch)
    df0 = _dataset(adir)
    uid_by_title = _keys(df0)
    au_by_uid_before = {dio_key(u): a for u, a in zip(df0["UID"], df0["AU"])}
    split = _split_for(adir)

    # 1) delete row 0 (comes BEFORE the targets) by UID
    r = client.post(f"/api/projects/{pid}/records/delete", json={"uids": [uid_by_title["Title 0"]]})
    assert r.status_code == 200 and r.json()["deleted"] == 1
    # 2) filter-apply drops row 1 (year 1990), also before the targets
    r = client.post(f"/api/projects/{pid}/filter/apply", json={"spec": {"year": {"min": 2000}}})
    assert r.status_code == 200 and r.json()["removed"] == 1
    now = _dataset(adir)
    assert len(now) == len(_ROWS) - 2
    # positions of the target rows really did shift
    assert list(now["TI"])[0] == "Title 2"

    # 3) apply the split; the request tries to smuggle its own groups/records - ignored
    smuggled = {"split_id": split["split_id"], "name": "ALPHA X",
                "groups": [{"suffix": "(z)", "records": [0, 1, 2, 3, 4, 5, 6]}]}
    r = _apply(client, pid, [smuggled])
    assert r.status_code == 200, r.text
    body = r.json()["split"]
    assert body["replacements"] == body["applied_rows"] == 2
    assert body["skipped_missing"] == 0
    assert set(body["changed_uids"]) == {uid_by_title["Title 5"], uid_by_title["Title 6"]}

    after = _dataset(adir)
    au_after = {dio_key(u): a for u, a in zip(after["UID"], after["AU"])}
    target = {uid_by_title["Title 5"], uid_by_title["Title 6"]}
    for uid, au in au_after.items():
        if uid in target:
            assert "SMITH J (b)" in [x.strip() for x in au.split(";")]
        else:
            assert au == au_by_uid_before[uid], (uid, au)           # no other record touched
    assert not any("(z)" in a or "ALPHA X (" in a for a in after["AU"])
    # the same-name decoy WITHOUT field data was not in any group and stays untouched
    assert au_after[uid_by_title["Title 7"]] == "SMITH J; ZED Q"
    # audit carries the counts
    audit = json.loads((adir / "disambiguation_audit_authors_split.json").read_text(encoding="utf-8"))
    assert audit[-1]["applied_rows"] == 2 and audit[-1]["skipped_missing"] == 0
    assert set(audit[-1]["changed_uids"]) == target


def test_split_lands_on_the_intended_records_after_a_snapshot_restore(client, monkeypatch):
    """scan -> delete (twice) -> restore the middle snapshot -> apply: the restored dataset has a
    different row set/order than at scan time; the suffix still lands on exactly the target UIDs."""
    pid, aid, adir = _make_project(client)
    _scan_authors(pid, monkeypatch)
    df0 = _dataset(adir)
    uid_by_title = _keys(df0)
    au_before = {dio_key(u): a for u, a in zip(df0["UID"], df0["AU"])}
    split = _split_for(adir)

    r1 = client.post(f"/api/projects/{pid}/records/delete", json={"uids": [uid_by_title["Title 0"]]})
    assert r1.status_code == 200
    r2 = client.post(f"/api/projects/{pid}/records/delete", json={"uids": [uid_by_title["Title 5"]]})
    assert r2.status_code == 200
    snap_before_second_delete = r2.json()["snapshot"]          # has Title 5 back, still lacks Title 0
    r3 = client.post(f"/api/projects/{pid}/records/restore-snapshot", json={"snapshot": snap_before_second_delete})
    assert r3.status_code == 200, r3.text
    now = _dataset(adir)
    now_keys = {dio_key(u) for u in now["UID"]}
    assert uid_by_title["Title 0"] not in now_keys and uid_by_title["Title 5"] in now_keys

    r = _apply(client, pid, [{"split_id": split["split_id"]}])
    assert r.status_code == 200, r.text
    body = r.json()["split"]
    target = {uid_by_title["Title 5"], uid_by_title["Title 6"]}
    assert body["applied_rows"] == 2 and body["skipped_missing"] == 0 and set(body["changed_uids"]) == target
    after = _dataset(adir)
    for uid, au in zip(after["UID"], after["AU"]):
        uid = dio_key(uid)
        if uid in target:
            assert "SMITH J (b)" in [x.strip() for x in au.split(";")]
        else:
            assert au == au_before[uid]


def test_a_deleted_target_uid_is_skipped_and_counted(client, monkeypatch):
    pid, aid, adir = _make_project(client)
    _scan_authors(pid, monkeypatch)
    df0 = _dataset(adir)
    uid_by_title = _keys(df0)
    split = _split_for(adir)

    # one of the two (b) records is deleted before the apply
    gone = uid_by_title["Title 6"]
    assert client.post(f"/api/projects/{pid}/records/delete", json={"uids": [gone]}).status_code == 200

    r = _apply(client, pid, [{"split_id": split["split_id"]}])
    assert r.status_code == 200, r.text
    body = r.json()["split"]
    assert body["applied_rows"] == 1 and body["skipped_missing"] == 1
    assert body["changed_uids"] == [uid_by_title["Title 5"]]
    after = _dataset(adir)
    assert gone not in {dio_key(u) for u in after["UID"]}
    assert sum("SMITH J (b)" in a for a in after["AU"]) == 1
    audit = json.loads((adir / "disambiguation_audit_authors_split.json").read_text(encoding="utf-8"))
    assert audit[-1]["skipped_missing"] == 1 and audit[-1]["applied_rows"] == 1


def test_all_targets_gone_is_a_noop_with_counts(client, monkeypatch):
    pid, aid, adir = _make_project(client)
    _scan_authors(pid, monkeypatch)
    df0 = _dataset(adir)
    uid_by_title = _keys(df0)
    split = _split_for(adir)
    gone = [uid_by_title["Title 5"], uid_by_title["Title 6"]]
    assert client.post(f"/api/projects/{pid}/records/delete", json={"uids": gone}).status_code == 200
    before = _dataset(adir)

    r = _apply(client, pid, [{"split_id": split["split_id"]}])
    assert r.status_code == 200
    body = r.json()["split"]
    assert body["replacements"] == 0 and body["skipped_missing"] == 2 and body["snapshot"] is None
    pd.testing.assert_frame_equal(_dataset(adir), before)


def test_unknown_split_id_is_skipped_and_counted(client, monkeypatch):
    pid, aid, adir = _make_project(client)
    _scan_authors(pid, monkeypatch)
    before = _dataset(adir)
    r = _apply(client, pid, [{"split_id": "does_not_exist",
                              "name": "SMITH J",
                              "groups": [{"suffix": "(b)", "records": [dio_key(u) for u in before["UID"]]}]}])
    assert r.status_code == 200
    body = r.json()["split"]
    assert body["replacements"] == 0 and body["skipped_unknown_split"] == 1
    pd.testing.assert_frame_equal(_dataset(adir), before)


def test_name_token_check_is_kept(client, monkeypatch):
    pid, aid, adir = _make_project(client)
    _scan_authors(pid, monkeypatch)
    split = _split_for(adir)
    # an edit between scan and apply renamed the author in one target row
    from services import dataset_io as dio
    df = _dataset(adir)
    uid5 = df.loc[df["TI"] == "Title 5", "UID"].iloc[0]
    df.loc[df["UID"] == uid5, "AU"] = "SMYTHE J; FOE D"
    dio.atomic_write_dataset(df, adir / "merged.parquet")

    r = _apply(client, pid, [{"split_id": split["split_id"]}])
    body = r.json()["split"]
    assert body["applied_rows"] == 1 and body["skipped_no_token"] == 1
    assert dict(zip(_dataset(adir)["UID"], _dataset(adir)["AU"]))[uid5] == "SMYTHE J; FOE D"


# ── stale proposal files ──────────────────────────────────────────────────

def test_legacy_proposals_with_int_records_are_refused(client, monkeypatch):
    pid, aid, adir = _make_project(client, with_uid=True)
    legacy = {
        "kind": "authors", "generated_at": 1.0, "clusters": [], "uncertain": [],
        "splits": [{"split_id": "smith_j_0", "name": "SMITH J", "tier": 1,
                    "groups": [{"records": [2, 3, 4], "suffix": ""}, {"records": [5, 6], "suffix": "(b)"}]}],
    }
    (adir / "disambiguation_authors.json").write_text(json.dumps(legacy), encoding="utf-8")
    before = _dataset(adir)
    r = _apply(client, pid, [{"split_id": "smith_j_0"}])
    assert r.status_code == 409 and r.json()["detail"] == "proposals_stale_rescan"
    pd.testing.assert_frame_equal(_dataset(adir), before)


def test_proposals_with_the_random_key_schema_are_refused(client, monkeypatch):
    """A proposal file written with the older random "r_..." keys (schema uid_v1) is stale."""
    pid, aid, adir = _make_project(client)
    _scan_authors(pid, monkeypatch)
    split = _split_for(adir)
    props = _proposals(adir)
    props["schema"] = "uid_v1"
    for g in split["groups"]:
        g["records"] = [f"r_{int(r):016x}" for r in g["records"]]
    props["splits"] = [split]
    (adir / "disambiguation_authors.json").write_text(json.dumps(props), encoding="utf-8")
    before = _dataset(adir)
    r = _apply(client, pid, [{"split_id": split["split_id"]}])
    assert r.status_code == 409 and r.json()["detail"] == "proposals_stale_rescan"
    pd.testing.assert_frame_equal(_dataset(adir), before)


def test_proposals_from_another_analysis_are_refused(client, monkeypatch):
    pid, aid, adir = _make_project(client)
    _scan_authors(pid, monkeypatch)
    split = _split_for(adir)
    props = _proposals(adir)
    props["analysis_id"] = "analysis_some_other"
    (adir / "disambiguation_authors.json").write_text(json.dumps(props), encoding="utf-8")
    before = _dataset(adir)
    r = _apply(client, pid, [{"split_id": split["split_id"]}])
    assert r.status_code == 409 and r.json()["detail"] == "proposals_stale_rescan"
    pd.testing.assert_frame_equal(_dataset(adir), before)


def test_apply_clusters_also_refuses_stale_files(client):
    pid, aid, adir = _make_project(client, with_uid=True)
    (adir / "disambiguation_affiliations.json").write_text(
        json.dumps({"kind": "affiliations", "clusters": [{"cluster_id": "c", "members": []}]}), encoding="utf-8")
    r = client.post(f"/api/projects/{pid}/disambiguate/apply",
                    json={"kind": "affiliations", "approved": [{"cluster_id": "c"}]})
    assert r.status_code == 409 and r.json()["detail"] == "proposals_stale_rescan"


def test_a_rescan_replaces_a_stale_file_and_apply_works_again(client, monkeypatch):
    pid, aid, adir = _make_project(client, with_uid=True)
    (adir / "disambiguation_authors.json").write_text(
        json.dumps({"kind": "authors", "clusters": [], "splits": [], "uncertain": []}), encoding="utf-8")
    assert _apply(client, pid, [{"split_id": "x"}]).status_code == 409
    _scan_authors(pid, monkeypatch)
    split = _split_for(adir)
    r = _apply(client, pid, [{"split_id": split["split_id"]}])
    assert r.status_code == 200 and r.json()["split"]["applied_rows"] == 2


# ── restore ───────────────────────────────────────────────────────────────

def test_disambiguation_restore_of_a_uidless_snapshot_yields_valid_uids(client, monkeypatch):
    from services import dataset_io as dio
    from services import storage

    pid, aid, adir = _make_project(client)
    snaps = adir / "snapshots"
    snaps.mkdir()
    snap = snaps / "pre_old.parquet"
    dio.atomic_write_dataset(_dataset(adir), snap)                  # no UID column
    assert "UID" not in dio.read_dataset(snap).columns

    rel = str(snap.relative_to(storage.settings.storage_path))
    r = client.post(f"/api/projects/{pid}/disambiguate/restore", json={"snapshot": rel})
    assert r.status_code == 200, r.text
    df = _dataset(adir)
    assert "UID" in df.columns and df["UID"].is_unique and len(df) == len(_ROWS)
    assert str(df["UID"].dtype) == "int64" and (df["UID"] > 0).all()
    dio.check_uid(df)


def test_disambiguation_restore_repairs_duplicate_uids(client):
    from services import dataset_io as dio
    from services import storage

    pid, aid, adir = _make_project(client)
    snaps = adir / "snapshots"
    snaps.mkdir()
    snap = snaps / "pre_dup.parquet"
    bad = _dataset(adir)
    bad["UID"] = "same"                                             # all duplicated, bypass the writer's check
    bad.to_parquet(snap, index=False)

    rel = str(snap.relative_to(storage.settings.storage_path))
    assert client.post(f"/api/projects/{pid}/disambiguate/restore", json={"snapshot": rel}).status_code == 200
    df = _dataset(adir)
    assert df["UID"].is_unique and str(df["UID"].dtype) == "int64" and (df["UID"] > 0).all()
