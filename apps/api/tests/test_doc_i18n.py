"""Tests for the language-aware operation-log documents (services.doc_i18n).

Covers: Markdown/TXT/PDF output in en and tr, title resolution from title_key,
number/percent/date formatting per language, key + placeholder parity with the
frontend audit titles, the localized data-flow PDF stage names, and the
analysis scoping of /report/log.*.
"""

import io
import json
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]
_WEB_I18N = _API_ROOT.parent / "web" / "lib" / "i18n"

_TR_LETTERS = re.compile("[çğıöşüÇĞİÖŞÜ]")


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("BIBEXPY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    sys.path.insert(0, str(_API_ROOT))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from main import app
    return TestClient(app)


def _seed(client):
    """Project with a smart merge, a fill_all, a borderline decision and a filter entry."""
    from services import analyses, audit

    pid = client.post("/api/projects", json={"name": "Docs"}).json()["id"]
    aid, _ = analyses.create_analysis(pid, "smart")
    analyses.finalize_analysis(pid, aid)

    audit.write(pid, kind="merge", title="Merge (smart)", title_key="audit.titles.merge",
                title_params={"method": "smart"}, details={
                    "method": "smart", "wos_input": 1417, "scopus_input": 1702,
                    "matched_pairs": 1144, "borderline_count": 6, "merged_count": 1972,
                    "conflict_count": 40, "lost_wos_count": 3, "lost_scopus_count": 5,
                    "duration_seconds": 12.5,
                    "match_stages": {"DOI exact": 1083, "Title+Year+Surname": 61},
                    "field_source_distribution": {"scopus": 30, "wos": 10},
                    "output_files": ["merged.xlsx"],
                }, analysis_id=aid)
    audit.write(pid, kind="enrich_api", title="Fill missing fields (API)",
                title_key="audit.titles.fillAll",
                details={"method": "fill_all", "filled": 1234}, user_action="auto", analysis_id=aid)
    audit.write(pid, kind="merge_borderline", title="Borderline pairs: 1 accepted / 2 rejected / 3 skipped",
                title_key="audit.titles.borderlineDecisions",
                title_params={"accept": 1, "reject": 2, "skip": 3},
                details={"applied_changes": 1}, user_action="borderline_decide", analysis_id=aid)
    audit.write(pid, kind="filter_apply", title="Filter applied to dataset: 1954 kept / 12 removed",
                title_key="audit.titles.filterApplied", title_params={"kept": 1954, "removed": 12},
                details={"before": 1966, "after": 1954, "removed": 12, "filter_keys": ["year", "doc_type"]},
                analysis_id=aid)
    return pid, aid


def test_markdown_and_txt_follow_language(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid, _ = _seed(client)

    # ── English: no Turkish letters anywhere ──
    for url in (f"/api/projects/{pid}/audit/report.md",
                f"/api/projects/{pid}/report/log.md",
                f"/api/projects/{pid}/report/log.txt"):
        r = client.get(url)
        assert r.status_code == 200
        assert not _TR_LETTERS.search(r.text), (url, _TR_LETTERS.findall(r.text))
    md_en = client.get(f"/api/projects/{pid}/report/log.md").text
    assert "Operation Report" in md_en
    assert "Chronology" in md_en and "Summary" in md_en
    assert "Merge (smart)" in md_en
    assert "Fill missing fields (API)" in md_en
    assert "Borderline pairs: 1 accepted / 2 rejected / 3 skipped" in md_en
    assert "Filter applied to dataset: 1,954 kept / 12 removed" in md_en
    assert "DOI exact" in md_en and "Union" not in md_en
    assert "1,417 WoS + 1,702 Scopus" in md_en
    assert "36.8%" in md_en  # (3119 - 1972) / 3119
    assert "Year, Document type" in md_en  # filter keys use the UI names

    # ── Turkish ──
    md_tr = client.get(f"/api/projects/{pid}/report/log.md?lang=tr").text
    assert "Operasyon Raporu" in md_tr
    assert "Kronoloji" in md_tr and "Özet" in md_tr
    assert "Birleştirme (smart)" in md_tr                 # title resolved from title_key
    assert "Eksik alanları doldur (API)" in md_tr
    assert "Belirsiz çiftler: 1 kabul / 2 red / 3 atlandı" in md_tr
    assert "Filtre veri setine uygulandı: 1.954 kayıt kaldı / 12 çıkarıldı" in md_tr
    assert "DOI tam eşleşme" in md_tr and "Başlık + yıl + soyad" in md_tr
    assert "1.417 WoS + 1.702 Scopus" in md_tr
    assert "%36,8" in md_tr
    assert "12,5 sn" in md_tr
    assert "Yıl, Doküman tipi" in md_tr
    assert re.search(r"_\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}_", md_tr)   # dd.mm.yyyy
    assert re.search(r"_\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}_", md_en)    # yyyy-mm-dd

    txt_tr = client.get(f"/api/projects/{pid}/report/log.txt?lang=tr").text
    assert "Operasyon Raporu" in txt_tr and "Kronoloji" in txt_tr
    assert "Birleştirme (smart)" in txt_tr
    # unknown language codes fall back to English
    assert "Operation Report" in client.get(f"/api/projects/{pid}/report/log.md?lang=xx").text


def test_log_scope_matches_audit_report(monkeypatch, tmp_path):
    """/report/log.* and /audit/report.md share the active-analysis scope."""
    client = _client(monkeypatch, tmp_path)
    pid, aid = _seed(client)
    from services import analyses, audit

    other, _ = analyses.create_analysis(pid, "smart")
    analyses.finalize_analysis(pid, other)
    audit.write(pid, kind="export", title="Other analysis export", analysis_id=other)
    audit.write(pid, kind="upload", title="Setup upload", analysis_id=None)
    analyses.set_active_analysis(pid, aid)

    def body(url):
        text = client.get(url).text
        # ignore the volatile "Generated" line
        return "\n".join(ln for ln in text.splitlines() if not ln.startswith("**Generated"))

    log_md = body(f"/api/projects/{pid}/report/log.md")
    audit_md = body(f"/api/projects/{pid}/audit/report.md")
    assert log_md == audit_md
    assert "Setup upload" in log_md
    assert "Other analysis export" not in log_md


def test_pdf_turkish_text_extractable(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid, _ = _seed(client)
    from services import report_flow

    r = client.get(f"/api/projects/{pid}/report/log.pdf?lang=tr")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    r_en = client.get(f"/api/projects/{pid}/report/log.pdf")
    assert r_en.status_code == 200 and r_en.content.startswith(b"%PDF")

    _regular, _bold, unicode_ok = report_flow._fonts()
    try:
        from pypdf import PdfReader
    except Exception:
        pytest.skip("pypdf not installed; font registration path only")
    text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages)
    meta_title = PdfReader(io.BytesIO(r.content)).metadata.title or ""
    if unicode_ok:
        assert "Kronoloji" in text and "Özet" in text
        assert "Birleştirme" in text
        assert "Operasyon Raporu" in meta_title
    else:  # ASCII fallback: Turkish letters folded, nothing corrupted
        assert "Ozet" in text and "Birlestirme" in text
    en_text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r_en.content)).pages)
    assert "Chronology" in en_text


def test_title_resolution_and_fallback():
    sys.path.insert(0, str(_API_ROOT))
    from services import doc_i18n

    entry = {"title": "stored", "title_key": "audit.titles.recordsDeleted", "title_params": {"n": 1500}}
    assert doc_i18n.resolve_title("en", entry) == "1,500 records deleted"
    assert doc_i18n.resolve_title("tr", entry) == "1.500 kayıt silindi"
    # unknown key -> stored title
    assert doc_i18n.resolve_title("tr", {"title": "stored", "title_key": "audit.titles.nope"}) == "stored"
    assert doc_i18n.resolve_title("tr", {"title": "stored"}) == "stored"
    # tr_ fallbacks: missing in tr -> English -> key
    assert doc_i18n.tr_("tr", "no.such.key") == "no.such.key"
    assert doc_i18n.tr_("fr", "doc.summary") == "Summary"
    # stage names by label and by code
    assert doc_i18n.stage_label("tr", "DOI exact") == doc_i18n.stage_label("tr", "1_doi_exact")
    assert doc_i18n.stage_label("tr", "4_journal_vol_page") == "Dergi + cilt + sayfa"
    assert doc_i18n.stage_label("tr", "Something else") == "Something else"
    assert doc_i18n.field_source_label("tr", "scopus_fallback") == "Scopus (yedek)"
    # number formats
    assert doc_i18n.fmt_int(1234567, "en") == "1,234,567"
    assert doc_i18n.fmt_int(1234567, "tr") == "1.234.567"
    assert doc_i18n.fmt_percent(0.125, "en") == "12.5%"
    assert doc_i18n.fmt_percent(0.125, "tr") == "%12,5"


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", text))


def test_audit_title_parity_with_frontend_json():
    if not (_WEB_I18N / "en.json").exists() or not (_WEB_I18N / "tr.json").exists():
        pytest.skip(f"frontend i18n files not found at {_WEB_I18N} (e.g. running from a wheel)")
    sys.path.insert(0, str(_API_ROOT))
    from services import doc_i18n

    for lang in ("en", "tr"):
        titles = json.loads((_WEB_I18N / f"{lang}.json").read_text(encoding="utf-8"))["audit"]["titles"]
        for key, value in titles.items():
            full = f"audit.titles.{key}"
            assert full in doc_i18n.STRINGS[lang], f"doc_i18n[{lang}] is missing {full}"
            assert _placeholders(doc_i18n.STRINGS[lang][full]) == _placeholders(value), (lang, full)
        # no stale keys either
        ours = {k for k in doc_i18n.STRINGS[lang] if k.startswith("audit.titles.")}
        assert ours == {f"audit.titles.{k}" for k in titles}, (lang, ours ^ {f"audit.titles.{k}" for k in titles})
    # the two dictionaries expose the same key set
    assert set(doc_i18n.STRINGS["en"]) == set(doc_i18n.STRINGS["tr"])


def test_flow_pdf_stage_names_localized():
    sys.path.insert(0, str(_API_ROOT))
    from services import report_flow

    flow = {
        "inputs": {"wos": 1417, "scopus": 1702, "total": 3119},
        "intra_removed": 3, "matched_pairs": 1144,
        "stages": {"DOI exact": 1083, "Title+Year+Surname": 61},
        "borderline_total": 6, "after_merge": 1972, "final_total": 1951,
        "steps": [{"kind": "filter_apply", "removed": 12, "after": 1954,
                   "criteria": ["year", "citation_count"]}],
    }
    tr_lines = [ln for b in report_flow._build_boxes(flow, "tr") for ln in b["lines"] + b["side"]]
    joined_tr = "\n".join(tr_lines)
    assert "DOI tam eşleşme: 1.083" in joined_tr
    assert "Başlık + yıl + soyad: 61" in joined_tr
    assert "Filtre uygulandı: Yıl, Atıf sayısı" in joined_tr
    assert "DOI exact" not in joined_tr

    en_lines = [ln for b in report_flow._build_boxes(flow, "en") for ln in b["lines"] + b["side"]]
    joined_en = "\n".join(en_lines)
    assert "DOI exact: 1,083" in joined_en
    assert "Filter applied: Year, Citation count" in joined_en
    assert not _TR_LETTERS.search(joined_en)


def test_flow_pdf_metadata_title_localized(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid, _ = _seed(client)
    r_tr = client.get(f"/api/projects/{pid}/report/flow.pdf?lang=tr")
    r_en = client.get(f"/api/projects/{pid}/report/flow.pdf")
    assert r_tr.status_code == 200 and r_en.status_code == 200
    try:
        from pypdf import PdfReader
    except Exception:
        pytest.skip("pypdf not installed")
    assert "Veri Akış Şeması" in (PdfReader(io.BytesIO(r_tr.content)).metadata.title or "")
    assert "Data Flow Diagram" in (PdfReader(io.BytesIO(r_en.content)).metadata.title or "")


def test_audit_entry_stores_title_key(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = client.post("/api/projects", json={"name": "K"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/audit", json={
        "kind": "export", "title": "Export (csv): a.csv",
        "title_key": "audit.titles.exported", "title_params": {"fmt": "csv", "name": "a.csv"},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["title_key"] == "audit.titles.exported"
    assert body["title_params"] == {"fmt": "csv", "name": "a.csv"}
    assert "Dışa aktarma (csv): a.csv" in client.get(f"/api/projects/{pid}/report/log.md?lang=tr").text


def test_digest_drops_free_text():
    sys.path.insert(0, str(_API_ROOT))
    from services import report_export

    out = report_export._clean_metrics({
        "method": "smart", "n": 5, "note": "Kullanıcı elle düzenledi", "names": ["ok_1", "Çok uzun metin burada"],
    })
    assert out == {"method": "smart", "n": 5, "names": ["ok_1"]}

    # nested dicts are cleaned too: free text inside a dict does not reach the digest
    nested = report_export._clean_metrics({
        "spec": {"year": {"min": 2010, "max": 2020}, "fulltext": {"query": "çocuk güvenliği"}},
        "only_text": {"note": "şu açıklama"},
    })
    assert nested == {"spec": {"year": {"min": 2010, "max": 2020}}}
    assert not any(ch in json.dumps(nested, ensure_ascii=False) for ch in "çğış")


def test_corrupt_entries_do_not_break_documents(monkeypatch, tmp_path):
    """Malformed stored values must not turn a download into a 500."""
    client = _client(monkeypatch, tmp_path)
    pid = client.post("/api/projects", json={"name": "Corrupt"}).json()["id"]
    from services import audit

    audit.write(pid, kind="merge", title="m", details={
        "method": "smart", "wos_input": "abc", "scopus_input": None, "duration_seconds": "x"},
        analysis_id=None)
    audit.write(pid, kind="export", title=None, title_key="audit.titles.recordsDeleted",
                title_params={"n": "abc"}, details={"c": float("nan")}, analysis_id=None)
    for fmt in ("md", "txt", "pdf"):
        for lang in ("en", "tr"):
            r = client.get(f"/api/projects/{pid}/report/log.{fmt}?lang={lang}")
            assert r.status_code == 200, (fmt, lang)


def test_title_params_named_key_or_lang_do_not_break_resolution():
    """POST /audit stores title_params verbatim; names that clash with tr_'s own
    parameters ("key", "lang") must not make every log/report download fail."""
    from services import doc_i18n

    entry = {"title_key": "audit.titles.exported", "title": "stored",
             "title_params": {"fmt": "XLSX", "name": "x.xlsx", "key": "a", "lang": "b"}}
    assert doc_i18n.resolve_title("en", entry) == doc_i18n.tr_("en", "audit.titles.exported", fmt="XLSX", name="x.xlsx")
    assert doc_i18n.resolve_title("tr", entry)
