"""Tests for the data-flow builder (services.report_flow).

The flow is derived from audit-log entries; tests write synthetic entries
mirroring a real pipeline (merge -> borderline accept -> filter apply ->
manual delete) and assert the derived counts.
"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("BIBEXPY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    sys.path.insert(0, str(_API_ROOT))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from main import app
    return TestClient(app)


def test_flow_from_recorded_operations(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from services import analyses, audit

    pid = client.post("/api/projects", json={"name": "Flow"}).json()["id"]
    aid, _ = analyses.create_analysis(pid, "smart")
    analyses.finalize_analysis(pid, aid)

    # 1) Smart Merge summary (the schema written by the real run_smart_merge)
    audit.write(pid, kind="merge", title="merge", details={
        "method": "smart", "wos_input": 1417, "scopus_input": 1702,
        "matched_pairs": 1144, "intra_wos_removed": 1, "intra_scopus_removed": 2,
        "match_stages": {"DOI exact": 1083, "Title+Year+Surname": 61},
        "borderline_count": 6, "merged_count": 1972,
    }, analysis_id=aid)
    # 2) Borderline acceptances (6 pairs approved -> 6 records removed)
    audit.write(pid, kind="merge_borderline", title="bl",
                details={"applied_changes": 6}, analysis_id=aid)
    # 3) Permanent filter (year range) — 12 records removed
    audit.write(pid, kind="filter_apply", title="filter", details={
        "before": 1966, "after": 1954, "removed": 12, "filter_keys": ["year"],
    }, analysis_id=aid)
    # 4) Manual delete — 3 records
    audit.write(pid, kind="records_delete", title="del",
                details={"deleted": 3}, after={"total": 1951}, analysis_id=aid)

    flow = client.get(f"/api/projects/{pid}/report/flow").json()
    assert flow["has_merge"] is True
    assert flow["inputs"] == {"wos": 1417, "scopus": 1702, "total": 3119}
    assert flow["intra_removed"] == 3
    assert flow["matched_pairs"] == 1144
    assert flow["stages"]["DOI exact"] == 1083
    assert flow["borderline_total"] == 6
    assert flow["after_merge"] == 1972

    kinds = [s["kind"] for s in flow["steps"]]
    assert kinds == ["merge_borderline", "filter_apply", "records_delete"]
    assert flow["steps"][1]["removed"] == 12
    assert flow["steps"][1]["criteria"] == ["year"]
    assert flow["steps"][2]["after"] == 1951
    # no merged.xlsx -> final_total falls back to None (the live count is optional)
    assert flow["final_total"] is None

    # Vector PDF output — EN and TR
    r = client.get(f"/api/projects/{pid}/report/flow.pdf")
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")
    assert len(r.content) > 1500
    assert "application/pdf" in r.headers["content-type"]

    r_tr = client.get(f"/api/projects/{pid}/report/flow.pdf?lang=tr")
    assert r_tr.status_code == 200
    assert r_tr.content.startswith(b"%PDF")
    assert len(r_tr.content) > 1500


def test_flow_without_merge_is_empty(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = client.post("/api/projects", json={"name": "Empty"}).json()["id"]
    flow = client.get(f"/api/projects/{pid}/report/flow").json()
    assert flow["has_merge"] is False
    assert flow["inputs"] is None
    assert flow["steps"] == []
    # If there is no merge, there is no diagram to draw either -> 409
    assert client.get(f"/api/projects/{pid}/report/flow.pdf").status_code == 409
