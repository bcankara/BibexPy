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

from services.smart_merger import compute_match, doi_conflict, negative_rule_check  # noqa: E402


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


# ── negative_rule_check ───────────────────────────────────────────────────

def test_negrule_different_doi_rejects():
    msg = negative_rule_check(_rec(doi="10.1/aaa"), _rec(doi="10.1/bbb"))
    assert msg is not None and "DOI" in msg


def test_negrule_same_doi_passes():
    assert negative_rule_check(_rec(doi="10.1/aaa"), _rec(doi="10.1/aaa")) is None


def test_negrule_one_side_missing_doi_passes():
    # Yalnız bir tarafta DOI → çelişki yok, başlık eşleştirmesine düşülmeli
    assert negative_rule_check(_rec(doi="10.1/aaa"), _rec(doi=None)) is None
    assert negative_rule_check(_rec(doi=None), _rec(doi=None)) is None


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
