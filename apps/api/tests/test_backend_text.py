"""Backend text policy: English canonical text, machine codes for errors.

(a) A lint that fails when Turkish letters appear in string literals, docstrings or
    comments of the backend code (user-facing Turkish lives in the frontend i18n files
    and in the en/tr document dictionary ``services/doc_i18n.py``).
(b) Behaviour tests for the English borderline reasons, structured borderline fields,
    coded errors and language-neutral analysis labels.
"""

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _API_ROOT.parents[1]

_TURKISH = re.compile("[çğıöşüÇĞİÖŞÜ]")

# Whole files that legitimately hold Turkish text (the en/tr document dictionary).
_ALLOW_FILES = {"doc_i18n.py"}

# Per-file substrings of data literals/comments that must keep Turkish letters.
_ALLOW_SNIPPETS = {
    "c1_utils.py": ["türkiye", "çğıöşü"],   # country map + letter class
    "MergeDB.py": ["FRANÇAIS"],                                          # WoS language value
}
# Files holding single-character transliteration (fold) tables.
_FOLD_TABLE_FILES = {"report_flow.py", "api_utils.py"}
# Author name inside the citation text (methodology, LLM prompt, comments).
_AUTHOR = "Şahin"


def _scan_roots() -> list[Path]:
    return [_API_ROOT, _REPO_ROOT / "packages" / "bibex_core"]


def _py_files() -> list[Path]:
    files: list[Path] = []
    for root in _scan_roots():
        for p in root.rglob("*.py"):
            parts = set(p.parts)
            if "tests" in parts or "__pycache__" in parts or "node_modules" in parts:
                continue
            if p.name in _ALLOW_FILES:
                continue
            files.append(p)
    return sorted(files)


def _is_allowed(path: Path, text: str) -> bool:
    cleaned = text.replace(_AUTHOR, "")
    for snippet in _ALLOW_SNIPPETS.get(path.name, []):
        cleaned = cleaned.replace(snippet, "")
    # A lone transliteration-table character (ASCII folding maps in report_flow and
    # api_utils, e.g. dotless i -> i) is data, not user-facing text.
    if len(text) == 1 and path.name in _FOLD_TABLE_FILES:
        return True
    return not _TURKISH.search(cleaned)


def _rel(p: Path) -> str:
    try:
        return p.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def test_no_turkish_in_backend_string_literals():
    offenders: list[str] = []
    for path in _py_files():
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if not _is_allowed(path, node.value):
                    snippet = node.value.strip().replace("\n", " ")[:70]
                    offenders.append(f"{_rel(path)}:{node.lineno}: {snippet!r}")
    assert not offenders, "Turkish text in backend string literals:\n" + "\n".join(offenders)


def test_no_turkish_in_backend_comments():
    offenders: list[str] = []
    for path in _py_files():
        src = path.read_text(encoding="utf-8")
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT and not _is_allowed(path, tok.string):
                offenders.append(f"{_rel(path)}:{tok.start[0]}: {tok.string.strip()[:70]!r}")
    assert not offenders, "Turkish text in backend comments:\n" + "\n".join(offenders)


# ── (b) behaviour ────────────────────────────────────────────────────────

def _rec(doi, title, year=2020, surname="smith"):
    from services.smart_merger import normalize_doi
    return {
        "_norm_doi": normalize_doi(doi), "_norm_title": title, "_norm_year": year,
        "_norm_surname": surname, "_norm_pmid": None, "_norm_issn": None,
        "_norm_journal": "journal of global health",
    }


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("BIBEXPY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    sys.path.insert(0, str(_API_ROOT))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from main import app
    return TestClient(app)


def test_stage3_reason_is_english_and_ascii():
    sys.path.insert(0, str(_API_ROOT))
    from services.smart_merger import compute_match
    m = compute_match(_rec("10.1000/a", "resource scarcity and child health outcomes"),
                      _rec("10.1000/b", "resource scarcity and child health outcomes"))
    assert m is None  # distinct non-variant DOIs veto the pair

    m = compute_match(_rec(None, "resource scarcity and child health outcomes"),
                      _rec(None, "resource scarcity and child health outcomes"))
    assert m["stage"] == "3_title_year_surname"
    assert ">=" in m["reason"] and "surname 'smith' matched" in m["reason"]
    assert m["reason"].isascii()
    assert m["doi_variant"] is False


def test_doi_variant_reason_uses_ascii_arrow():
    sys.path.insert(0, str(_API_ROOT))
    from services.smart_merger import compute_match
    m = compute_match(_rec("10.7189/jogh.12-05057", "resource scarcity and child health outcomes"),
                      _rec("10.7189/JOGH.12.05057", "resource scarcity and child health outcomes"))
    assert m["doi_variant"] is True
    assert "DOI spelling variant:" in m["reason"] and "<->" in m["reason"]
    assert m["reason"].isascii()


def test_stage1_keeps_parseable_doi_and_exposes_it():
    sys.path.insert(0, str(_API_ROOT))
    from services.smart_merger import compute_match
    m = compute_match(_rec("10.1000/abc", "a"), _rec("10.1000/ABC", "b"))
    assert m["reason"] == "DOI exact: 10.1000/abc"
    assert m["doi"] == "10.1000/abc"


def test_borderline_row_helpers():
    sys.path.insert(0, str(_API_ROOT))
    from services.smart_merger import _opt_bool, _opt_int, _row_doi_variant
    assert _opt_int(float("nan")) is None and _opt_int(1.0) == 1
    assert _opt_bool(float("nan")) is None and _opt_bool("True") is True
    assert _row_doi_variant(pd.Series({"doi_variant": True})) is True
    assert _row_doi_variant(pd.Series({"reason": "x | DOI spelling variant: a <-> b"})) is True
    assert _row_doi_variant(pd.Series({"reason": "JW(title)=0.9"})) is False


def test_list_borderline_blank_cells_are_not_nan_strings(api):
    """DOI-less pairs are stored as empty xlsx cells; they must not come back as "nan"."""
    from services import analyses, smart_merger
    pid = api.post("/api/projects", json={"name": "T"}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "smart")
    analyses.set_active_analysis(pid, aid)
    pd.DataFrame([{
        "pair_id": "p000001", "wos_index": 0, "scp_index": 1, "jw_title": 0.85,
        "year_diff": 0, "surname_match": True, "doi_variant": False, "confidence": 0.8,
        "stage_label": "Borderline (manual review)", "reason": "JW(title)=0.850",
        "wos_doi": "", "scp_doi": "", "wos_title": "t", "scp_title": "t",
        "wos_year": 2020, "scp_year": 2020, "wos_surname": "", "scp_surname": "",
        "wos_journal": "", "scp_journal": "", "wos_volume": "", "scp_volume": "",
    }]).to_excel(adir / "borderline_queue.xlsx", index=False)
    items = smart_merger.list_borderline(pid)
    assert len(items) == 1
    it = items[0]
    assert it["wos_doi"] is None and it["scp_doi"] is None
    assert it["wos"]["doi"] is None and it["wos"]["surname"] is None
    assert it["year_diff"] == 0 and it["surname_match"] is True and it["doi_variant"] is False


def test_shortcuts_have_stable_ids_and_english_labels(tmp_path):
    sys.path.insert(0, str(_API_ROOT))
    for mod in list(sys.modules):
        if mod.startswith("routers.system"):
            sys.modules.pop(mod, None)
    from routers.system import _shortcuts
    (tmp_path / "Documents").mkdir()
    items = _shortcuts(tmp_path)
    ids = [it["id"] for it in items]
    assert ids[0] == "home" and "documents" in ids
    assert all(it["label"].isascii() for it in items)


def test_suggest_folders_have_ids(api):
    r = api.get("/api/projects/x/export-folder/suggest")
    assert r.status_code == 200
    sugg = r.json()["suggestions"]
    assert [s["id"] for s in sugg] == ["downloads", "documents", "desktop", "bibexpy_output"]
    assert all(s["label"].isascii() for s in sugg)


def test_settings_labels_english_and_openalex_key(api):
    r = api.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    fields = {f["key"]: f for f in body["fields"]}
    assert fields["OPENALEX_API_KEY"]["secret"] is True
    assert fields["OPENALEX_API_KEY"]["group"] == "api"
    keys = list(fields)
    assert keys.index("OPENALEX_API_KEY") == keys.index("CROSSREF_EMAIL") + 1
    assert all(not _TURKISH.search(f["label"] + (f.get("hint") or "")) for f in fields.values())
    assert all(not _TURKISH.search(n) for n in body["notes"])


def test_validate_path_messages_are_english(api, tmp_path):
    r = api.post("/api/settings/validate-path", json={"path": ""})
    assert r.json()["message"] == "Path is empty"
    r = api.post("/api/settings/validate-path", json={"path": str(tmp_path / "missing")})
    assert "does not exist" in r.json()["message"]


def test_coded_errors(api):
    r = api.post("/api/projects", json={"name": "T"})
    pid = r.json()["id"]
    # No merged dataset -> coded 409 from the exporter
    r = api.post(f"/api/projects/{pid}/export", json={"fmt": "csv"})
    assert r.status_code == 409
    assert r.json()["detail"] == "no_merged_data"
    # Unknown project -> project_not_found, not free text
    r = api.post("/api/projects/nope/export", json={"fmt": "csv"})
    assert r.json()["detail"] == "project_not_found"


def test_converter_errors_are_codes(api):
    sys.path.insert(0, str(_API_ROOT))
    from fastapi import HTTPException
    from services import converter
    with pytest.raises(HTTPException) as ei:
        converter.csv_to_xlsx("nonexistent", ["a.csv"])
    assert ei.value.detail == "project_not_found"


def test_filter_engine_load_merged_code(api):
    from services import filter_engine
    pid = api.post("/api/projects", json={"name": "T"}).json()["id"]
    with pytest.raises(FileNotFoundError) as ei:
        filter_engine.load_merged(pid)
    assert str(ei.value) == "no_merged_data"


def test_decide_borderline_without_analysis_raises_code(api):
    from services import smart_merger
    pid = api.post("/api/projects", json={"name": "T"}).json()["id"]
    with pytest.raises(RuntimeError, match=r"^no_active_analysis$"):
        smart_merger.decide_borderline(pid, [{"pair_id": "p000001", "decision": "accept"}])


def test_auto_analysis_label_is_language_neutral(api):
    from services import analyses
    pid = api.post("/api/projects", json={"name": "T"}).json()["id"]
    aid, _ = analyses.create_analysis(pid, "smart")
    meta = analyses.get_analysis_meta(pid, aid)
    assert meta["label_auto"] is True
    assert re.fullmatch(r"Smart — \d{4}-\d{2}-\d{2} \d{2}:\d{2}", meta["label"])
    aid2, _ = analyses.create_analysis(pid, "smart", label="My run")
    meta2 = analyses.get_analysis_meta(pid, aid2)
    assert meta2["label_auto"] is False and meta2["label"] == "My run"
    listed = {a["id"]: a for a in analyses.list_analyses(pid)}
    assert listed[aid]["label_auto"] is True and listed[aid2]["label_auto"] is False
