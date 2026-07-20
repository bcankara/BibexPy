"""Tests for the Smart Merge DOI conflict rule.

Verifies that when both records have normalized but differing DOIs, they are
never treated as the same publication regardless of title or journal similarity:
they neither auto-merge nor enter the borderline (manual review) queue. Exercises
negative_rule_check, doi_conflict, and compute_match in services.smart_merger.
"""

import sys
from pathlib import Path

# apps/api'yi import yoluna ekle (diğer testlerle aynı desen)
_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

import pandas as pd  # noqa: E402

from services.smart_merger import (  # noqa: E402
    compute_match,
    dedup_within_source,
    doi_conflict,
    generate_candidates,
    normalize_doi,
)


def _rec(doi=None, title="deep learning bibliometric analysis", year=2020,
         surname="SMITH", pmid=None, issn=None, journal="scientometrics"):
    """compute_match'in okuduğu _norm_* alanlarıyla minimal bir kayıt sözlüğü."""
    return {
        "_norm_doi": doi,
        "_norm_title": title,
        "_norm_year": year,
        "_norm_surname": surname,
        "_norm_pmid": pmid,
        "_norm_issn": issn,
        "_norm_journal": journal,
    }


# ── Kimlik hiyerarşisi: DOI > PMID > ISSN ─────────────────────────────────
# Üst seviyedeki KESİN eşitlik alt seviyedeki çelişkiyi geçersiz kılar.

def test_same_doi_overrides_issn_conflict():
    """GERÇEK VAKA: WoS print-ISSN, Scopus e-ISSN verir — aynı DOI'li çiftte
    ISSN çelişkisi merge'i vetolayamaz (eski davranış: sessiz duplike)."""
    w = _rec(doi="10.1/same", issn="12345678")
    s = _rec(doi="10.1/same", issn="87654321")
    m = compute_match(w, s)
    assert m is not None and m["stage"] == "1_doi_exact"


def test_same_doi_overrides_pmid_conflict():
    w = _rec(doi="10.1/same", pmid="111")
    s = _rec(doi="10.1/same", pmid="222")
    m = compute_match(w, s)
    assert m is not None and m["stage"] == "1_doi_exact"


def test_same_pmid_overrides_issn_conflict():
    w = _rec(pmid="333", issn="11112222")
    s = _rec(pmid="333", issn="33334444")
    m = compute_match(w, s)
    assert m is not None and m["stage"] == "2_pmid_exact"


def test_issn_conflict_still_blocks_title_stages():
    """DOI/PMID karşılaştırılamıyorsa ISSN çelişkisi başlık aşamalarını korur."""
    w = _rec(issn="11112222")
    s = _rec(issn="33334444")
    assert compute_match(w, s) is None


def test_pmid_conflict_rejects_without_doi():
    assert compute_match(_rec(pmid="1"), _rec(pmid="2")) is None


# ── compute_match: DOI belirleyici ────────────────────────────────────────

def test_identical_title_but_different_doi_is_not_a_match():
    """Aynı başlık+yıl+soyad (DOI'siz olsa Stage 3 @0.95 birleşirdi) ama DOI farklı
    → eşleşme YOK (None). Yanlış otomatik-birleştirmeyi önler."""
    w = _rec(doi="10.1/aaa")
    s = _rec(doi="10.1/bbb")
    assert compute_match(w, s) is None


def test_identical_title_without_dois_still_matches_control():
    """Kontrol: aynı iki kayıttan DOI'ler kaldırılınca Stage 3 birleşmesi geri gelir
    — yani reddi tetikleyen tek fark DOI çelişkisidir."""
    w = _rec(doi=None)
    s = _rec(doi=None)
    m = compute_match(w, s)
    assert m is not None
    assert m["stage"] == "3_title_year_surname"


def test_different_doi_never_enters_borderline_queue():
    """Borderline aralığına yakın (hafifçe farklı) başlıklarda bile DOI farklıysa
    sonuç None olmalı — uncertain pairs kuyruğuna girmemeli."""
    w = _rec(doi="10.1/aaa", title="machine learning citation network analysis")
    s = _rec(doi="10.1/bbb", title="machine learning citation networks analysis")
    m = compute_match(w, s)
    assert m is None


def test_same_doi_merges_stage1():
    """Regresyon: eşit DOI hâlâ Stage 1 (DOI exact) birleşmesi vermeli."""
    w = _rec(doi="10.1/aaa", title="alpha")
    s = _rec(doi="10.1/aaa", title="beta")  # başlıklar farklı olsa da DOI eşit → merge
    m = compute_match(w, s)
    assert m is not None
    assert m["stage"] == "1_doi_exact"
    assert m["confidence"] == 1.00


def test_one_side_missing_doi_can_still_match_on_title():
    """Yalnız bir tarafta DOI varsa DOI kuralı uygulanmaz; başlık eşleşmesi sürer."""
    w = _rec(doi="10.1/aaa")
    s = _rec(doi=None)
    m = compute_match(w, s)
    assert m is not None
    assert m["stage"] == "3_title_year_surname"


# ── doi_conflict: list_borderline güvenlik katmanı ────────────────────────

def test_doi_conflict_detects_different_after_normalize():
    """Prefix/case farklı olsa da normalize sonrası DOI'ler FARKLIYSA çelişki var."""
    assert doi_conflict("https://doi.org/10.1/AAA", "10.1/bbb") is True


def test_doi_conflict_same_after_normalize_is_false():
    """URL prefix + büyük/küçük harf farkı normalize sonrası aynı DOI → çelişki YOK."""
    assert doi_conflict("https://doi.org/10.1/ABC", "10.1/abc") is False


def test_doi_conflict_one_or_both_missing_is_false():
    """Bir taraf (veya iki taraf) DOI'siz → çelişki yok; başlık eşleşmesine düşülür."""
    assert doi_conflict("10.1/aaa", "") is False
    assert doi_conflict(None, "10.1/bbb") is False
    assert doi_conflict(None, None) is False


def test_doi_conflict_invalid_doi_is_false():
    """'10.' ile başlamayan geçersiz değerler normalize'da None olur → çelişki yok."""
    assert doi_conflict("not-a-doi", "10.1/aaa") is False


# ── Jenerik kısa başlık koruması ──────────────────────────────────────────

def test_generic_short_title_not_auto_merged():
    """'Editorial' x2 — aynı yıl/yazar ama FARKLI cilt+sayfa: derginin editörü
    her sayıya bir editorial yazar; otomatik Stage 3 birleşmesi YANLIŞ olurdu."""
    w = {**_rec(title="editorial", journal="journal x"), "VL": "1", "BP": "1"}
    s = {**_rec(title="editorial", journal="journal x"), "VL": "2", "BP": "55"}
    assert compute_match(w, s) is None


def test_short_title_still_merges_via_stage4():
    """Kısa başlık + AYNI dergi+cilt+sayfa → Stage 4 hâlâ birleştirir."""
    w = {**_rec(title="editorial", journal="journal x"), "VL": "7", "BP": "1"}
    s = {**_rec(title="editorial", journal="journal x"), "VL": "7", "BP": "1"}
    m = compute_match(w, s)
    assert m is not None and m["stage"] == "4_journal_vol_page"


def test_informative_title_still_stage3():
    """Bilgilendirici başlıklar Stage 3'te birleşmeye devam eder (regresyon)."""
    m = compute_match(_rec(), _rec())
    assert m is not None and m["stage"] == "3_title_year_surname"


# ── Sayısal alan / DOI normalize kenar durumları ──────────────────────────

def test_numeric_vl_bp_float_vs_string_stage4():
    """xlsx float (100.0) vs WoS txt string ('100') — Stage 4 kaçırmamalı."""
    w = {**_rec(title="totally different alpha", journal="chem eng journal"), "VL": "100", "BP": "55"}
    s = {**_rec(title="unrelated beta heading", journal="chem eng journal"), "VL": 100.0, "BP": 55.0}
    m = compute_match(w, s)
    assert m is not None and m["stage"] == "4_journal_vol_page"


def test_normalize_doi_prefix_variants():
    assert normalize_doi("doi:10.1234/ABC") == "10.1234/abc"
    assert normalize_doi("DOI: 10.1234/abc") == "10.1234/abc"
    assert normalize_doi("https://doi.org/https://doi.org/10.1/x") == "10.1/x"
    assert normalize_doi("doi.org/10.1/y") == "10.1/y"
    assert normalize_doi("not-a-doi") is None


# ── generate_candidates: kimlik-öncelikli (blocking'i atlar) ──────────────

def _frame(rows):
    return pd.DataFrame([{
        "_norm_doi": None, "_norm_title": "", "_norm_year": None,
        "_norm_surname": "", "_norm_pmid": None, "_norm_issn": None,
        "_norm_ut": None, "_norm_journal": "", **r,
    } for r in rows])


def test_same_doi_escapes_blocking_regression():
    """GERÇEK VAKA (39 duplike grup): aynı DOI ama soyad FARKLI ayrışmış
    ('RAHIM' vs 'ABDUL RAHIM') ve/veya yıl farklı (early-access) → eski
    blocking-yalnız aday üretimi çifti hiç karşılaştırmıyordu. Kimlik-öncelikli
    üretimde Stage 1 (DOI exact) adayı MUTLAKA çıkmalı."""
    wos = _frame([{"_norm_doi": "10.1002/ceat.1", "_norm_title": "hydrogen fuel cell triz",
                   "_norm_year": 2024, "_norm_surname": "RAHIM"}])
    scp = _frame([{"_norm_doi": "10.1002/ceat.1", "_norm_title": "hydrogen fuel cell triz",
                   "_norm_year": 2025, "_norm_surname": "ABDUL RAHIM"}])
    cands = generate_candidates(wos, scp)
    assert len(cands) == 1
    assert cands[0][3]["stage"] == "1_doi_exact"


def test_same_pmid_escapes_blocking():
    wos = _frame([{"_norm_pmid": "12345", "_norm_title": "alpha", "_norm_year": 2020, "_norm_surname": "SMITH"}])
    scp = _frame([{"_norm_pmid": "12345", "_norm_title": "beta study", "_norm_year": 2021, "_norm_surname": "JONES"}])
    cands = generate_candidates(wos, scp)
    assert len(cands) == 1
    assert cands[0][3]["stage"] == "2_pmid_exact"


def test_blocking_pairs_not_duplicated_by_identifier_pass():
    """Aynı çift hem DOI indeksinden hem bloktan gelirse TEK aday üretilmeli."""
    wos = _frame([{"_norm_doi": "10.1/x", "_norm_title": "same title here",
                   "_norm_year": 2020, "_norm_surname": "SMITH"}])
    scp = _frame([{"_norm_doi": "10.1/x", "_norm_title": "same title here",
                   "_norm_year": 2020, "_norm_surname": "SMITH"}])
    cands = generate_candidates(wos, scp)
    assert len(cands) == 1


# ── dedup_within_source ───────────────────────────────────────────────────

def test_dedup_within_source_same_ut_keeps_richest():
    """GERÇEK VAKA: aynı WoS UT'li iki ISI satırı (üst üste binen export'lar).
    En dolu satır kalmalı."""
    df = _frame([
        {"_norm_ut": "wos:1", "TI": "Title", "AB": ""},
        {"_norm_ut": "wos:1", "TI": "Title", "AB": "An abstract makes this row richer"},
    ])
    out, removed = dedup_within_source(df)
    assert removed == 1 and len(out) == 1
    assert out.iloc[0]["AB"] != ""


def test_dedup_within_source_same_doi():
    df = _frame([
        {"_norm_doi": "10.1/dup", "TI": "A"},
        {"_norm_doi": "10.1/dup", "TI": "B"},
        {"_norm_doi": "10.1/other", "TI": "C"},
    ])
    out, removed = dedup_within_source(df)
    assert removed == 1 and len(out) == 2


def test_dedup_within_source_no_duplicates_untouched():
    df = _frame([
        {"_norm_ut": "wos:1", "_norm_doi": "10.1/a"},
        {"_norm_ut": "wos:2", "_norm_doi": "10.1/b"},
    ])
    out, removed = dedup_within_source(df)
    assert removed == 0 and len(out) == 2


def test_different_doi_blocks_stage4_journal_match():
    """DOI farklı + başlıklar alakasız (Stage 3/5 yok) ama dergi/cilt/sayfa aynı:
    DOI'siz olsa Stage 4 (Journal+Vol+Pages) birleşirdi; DOI çelişkisi None verir."""
    w = _rec(doi="10.1/aaa", title="alpha study one", journal="scientometrics")
    s = _rec(doi="10.1/bbb", title="totally unrelated heading two", journal="scientometrics")
    w.update({"VL": "10", "BP": "100"})
    s.update({"VL": "10", "BP": "100"})
    assert compute_match(w, s) is None
    # Kontrol: DOI'ler kaldırılınca Stage 4 (Journal+Vol+Pages) birleşmesi geri gelir.
    w["_norm_doi"] = None
    s["_norm_doi"] = None
    m = compute_match(w, s)
    assert m is not None
    assert m["stage"] == "4_journal_vol_page"
