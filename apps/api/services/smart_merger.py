"""Smart Merge pipeline for deduplicating and combining bibliographic sources.

Runs a self-contained six-phase pipeline independent of the core merge path:
  1. Normalize  : DOI, title, year, surname, ISSN/PMID/UT
  2. Block      : group candidates by (year, surname initial); WoS blocks also meet
                  the Scopus blocks of the adjacent years
  3. Match      : staged rules (negative rules, DOI, PMID/UT, title similarity, journal+volume+page, borderline)
  4. Field merge: fixed per-field source preferences (WoS, Scopus, union, cross-fill)
  5. Audit      : write match, conflict, and borderline-queue reports
  6. Borderline : manual review UI plus optional LLM assistance behind a feature flag
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import pandas as pd

from bibex_core.cr_normalize import count_refs, normalize_cr
from config import settings
from jobs.runner import JobContext, run_cpu
from services import analyses, audit, dataset_io, filter_engine, storage
from services.disambiguation.similarity import jaro_winkler, name_initials, normalize_name


# ════════════════════════════════════════════════════════════════════════
#  SABİTLER — Caputo (2024) field preferences
# ════════════════════════════════════════════════════════════════════════

# Reference: Caputo, A., Pizzi, S., Pellegrini, M. M., & Dabić, M. (2024).
# Automatic Merging of Scopus and Web of Science Data for Simplified and
# Effective Bibliometric Analysis. Annals of Data Science, 11(3), 1023–1047.
#
# Bu kurallar SABIT — UI'dan değiştirilemez. Akademik reproducibility için.
# Her dataset üzerinde Smart Merge'in v1.0 sürümü aynı sonucu üretmelidir.
FIELD_PREFERENCES: dict[str, str] = {
    # WoS-öncelikli (atıf metrikleri ve referans listeleri WoS'ta daha temiz)
    "TC": "wos",          # times cited
    "CR": "wos",          # cited references
    "NR": "wos",          # number of references
    # Scopus-öncelikli (özet, yazar listeleri Scopus'ta daha tam)
    "AB": "scopus",       # abstract
    "AU": "scopus",       # author short
    "AF": "scopus",       # author full
    "C1": "scopus",       # affiliations
    # Union (her ikisinin birleşimi, ; ile dedup'lu)
    "DE": "union",        # author keywords
    "ID": "union",        # keywords plus
    # Cross-fill (biri boşsa diğerinden doldur)
    "WC": "cross_fill_wos_first",
    "SC": "cross_fill_wos_first",
}
DEFAULT_PREFERENCE = "wos_first"  # diğer tüm alanlar için

# Eşik sabitleri
TITLE_EXACT_THRESHOLD = 0.92          # Stage 3
TITLE_BORDERLINE_LOW = 0.80           # Stage 5 alt sınır
YEAR_TOLERANCE = 1                    # ±1 yıl
JOURNAL_SIMILARITY = 0.90             # Stage 4

# Title normalize için stopwords
STOPWORDS: set[str] = {
    "the", "a", "an", "of", "in", "on", "and", "or", "for", "to", "with",
    "by", "from", "as", "at", "is", "are", "was", "were", "be", "been",
}


# ════════════════════════════════════════════════════════════════════════
#  FAZ 1 — Normalize fonksiyonları
# ════════════════════════════════════════════════════════════════════════

_DOI_PREFIX_RE = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)
# Alt çizgi + Unicode tire ailesi (hyphen, non-breaking hyphen, figure/en/em/
# horizontal dash) tek kanonik '-' işaretine katlanır.
_DOI_SEP_RE = re.compile(r"[_\u2010-\u2015]")
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
_WS_RE = re.compile(r"\s+")
_LATEX_RE = re.compile(r"\\[a-z]+\{[^}]*\}|\\[\\\\&%$#_{}~^]")
_ISSN_RE = re.compile(r"[^0-9Xx]")


def _to_str(v: Any) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v).strip()


def _present(v: Any) -> bool:
    """Bir KİMLİK değeri gerçekten dolu mu? (None / NaN / "" / "nan" → False)

    "Boş", pandas sürümüne göre üç ayrı şeye dönüşür: None (object dtype),
    NaN (pandas 3'ün `str` dtype'ı — üstelik TRUTHY) ve "" / "nan" (xlsx
    gidiş-dönüşü). Çıplak `if v` kontrolü NaN'ı dolu sanar ve boş kimlikleri
    birbirine eşit kabul eder; bu da DOI'siz kayıtların tek anahtarda toplanıp
    silinmesine ve DOI'siz ↔ DOI'li gerçek çiftlerin vetolanmasına yol açar.
    Kimlik karşılaştırmaları (DOI/PMID/UT/ISSN) YALNIZ bu yardımcıyı kullanır.
    """
    if v is None:
        return False
    try:
        if pd.isna(v):
            return False
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s != "" and s.lower() not in ("nan", "none", "null")


def _clean_num(v: Any) -> str:
    """Sayısal alan (VL/BP/PG) karşılaştırma formu: '100.0' → '100'."""
    s = _to_str(v)
    if re.fullmatch(r"\d+\.0+", s):
        return s.split(".", 1)[0]
    return s


def normalize_doi(raw: Any) -> Optional[str]:
    """Bir DOI'yi kanonik biçime indir.

    Örnekler:
      'https://doi.org/10.1234/ABC'  → '10.1234/abc'
      'http://dx.doi.org/10.x'        → '10.x'
      '10.1234/abc/'                  → '10.1234/abc'
      '10.4103/jgid.jgid_12_19'       → '10.4103/jgid.jgid-12-19'
    """
    s = _to_str(raw)
    if not s:
        return None
    s = s.lower()
    # Kirli veride önekler üst üste binebilir ('doi:', çift 'https://doi.org/');
    # değişmez hale gelene dek soy. 4 tur pratikte tüm kombinasyonları kapsar.
    for _ in range(4):
        prev = s
        s = _DOI_PREFIX_RE.sub("", s)
        s = re.sub(r"^doi\s*:\s*", "", s)
        s = re.sub(r"^doi\.org/", "", s)
        if s == prev:
            break
    s = s.rstrip("/. \t")
    # Ayırıcı katlaması: DOI'ler KANONİK biçimde karşılaştırılır, çünkü
    # dizinleyiciler ayırıcıyı farklı yazıyor — WoS aynı makaleyi
    # '10.4103/jgid.jgid_12_19', Scopus '10.4103/jgid.jgid-12-19' verir.
    # Bir registrant'ın YALNIZCA '_' ile '-' farkı olan iki DOI'yi FARKLI
    # eserler için üretmesi pratikte yok denecek kadar nadirdir; buna karşılık
    # mevcut davranış gerçek çiftleri fiilen yasaklıyor: doi_conflict() True
    # döndüğü an çift borderline kuyruğuna bile giremeden vetolanıyor
    # (belirleyici DOI kuralı kanonik form üzerinde işler).
    s = _DOI_SEP_RE.sub("-", s)
    if not s.startswith("10."):
        return None
    return s


def normalize_title(raw: Any) -> str:
    """Title'ı kanonik forma indir: Unicode NFKD → ASCII → lower → punct strip → stopwords."""
    s = _to_str(raw)
    if not s:
        return ""
    # LaTeX makro temizliği
    s = _LATEX_RE.sub(" ", s)
    # Unicode normalize
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    # Stopword removal
    tokens = [t for t in s.split() if t not in STOPWORDS]
    return " ".join(tokens)


def normalize_year(raw: Any) -> Optional[int]:
    """'2023.0' / '2023' / 2023 → 2023; geçersizler → None."""
    s = _to_str(raw)
    if not s:
        return None
    try:
        n = int(float(s))
        if 1900 <= n <= 2100:
            return n
        return None
    except (ValueError, TypeError):
        return None


def normalize_author_surname(raw: Any) -> str:
    """İlk yazarın soyadını uppercase ASCII normalize et.

    WoS 'Smith J;Lee K' veya Scopus 'Smith, John A.;Lee, Kim' her ikisi 'SMITH'.
    Boş ise '' döner.
    """
    s = _to_str(raw)
    if not s:
        return ""
    # İlk yazar
    first = re.split(r"[;|]", s, maxsplit=1)[0]
    # Scopus formatı: 'Smith, John A.' → 'Smith John A'
    first = first.replace(",", " ")
    surname, _initials = name_initials(first)
    return surname.upper()


def normalize_issn(raw: Any) -> Optional[str]:
    """ISSN'i 8 hane (rakam + X) forma indir; geçersiz → None."""
    s = _to_str(raw)
    if not s:
        return None
    s = _ISSN_RE.sub("", s.upper())
    if len(s) == 8:
        return s
    return None


def normalize_id_token(raw: Any) -> Optional[str]:
    """PMID / UT gibi genel token normalize: lowercase, whitespace strip."""
    s = _to_str(raw)
    if not s:
        return None
    s = s.lower().strip()
    return s or None


# ════════════════════════════════════════════════════════════════════════
#  FAZ 2 — Blocking
# ════════════════════════════════════════════════════════════════════════

def build_blocks(df: pd.DataFrame) -> dict[tuple[Optional[int], str], list[int]]:
    """key = (year, surname[0]) → satır indeksleri listesi.

    Year None ise (None, surname[0]) bloğuna düşer.
    Surname boş ise (year, '') bloğuna düşer.
    """
    blocks: dict[tuple[Optional[int], str], list[int]] = {}
    for idx, row in df.iterrows():
        year = row.get("_norm_year")
        surname = row.get("_norm_surname", "")
        first_letter = surname[0] if surname else ""
        key = (year, first_letter)
        blocks.setdefault(key, []).append(int(idx))
    return blocks


def dedup_within_source(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Aynı kaynak İÇİNDEKİ kimlik-duplikelerini ele (UT/EID ve DOI).

    Üst üste binen export dosyaları aynı kaydı iki kez getirebilir (aynı WoS UT)
    ya da aynı DOI'li iki satır bulunabilir. Cross-source eşleştirme yalnız
    WoS↔Scopus baktığından bunlar yakalanmıyordu; ayrıca kaynak-içi kopya varken
    greedy 1-1 eşleşme ikinci kopyayı açıkta bırakıyordu. Her kimlik grubunda
    EN DOLU satır (boş olmayan hücre sayısı en yüksek) tutulur.

    Dönüş: (dedup edilmiş df — orijinal index korunur, çıkarılan satır sayısı)
    """
    if df.empty:
        return df, 0
    # Doluluk skoru KOLON KOLON hesaplanır: df.astype(str) tüm frame'in string
    # kopyasını tek seferde materialize eder (100k satırlı object frame'de
    # yüzlerce MB ve pipeline'ın en yoğun tek numpy/SIMD patlaması). Kolon
    # başına birikim aynı sonucu çok daha düşük tepe bellekle verir.
    richness = pd.Series(0, index=df.index, dtype="int64")
    for col in df.columns:
        s = df[col]
        richness += s.notna().astype("int64") + (s.astype(str) != "").astype("int64")
    drop: set = set()
    for key_col in ("_norm_ut", "_norm_doi"):
        if key_col not in df.columns:
            continue
        best_for: dict[str, Any] = {}
        for idx in df.index:
            if idx in drop:
                continue
            v = df.at[idx, key_col]
            if not _present(v):
                continue
            prev = best_for.get(v)
            if prev is None:
                best_for[v] = idx
            elif richness[idx] > richness[prev]:
                drop.add(prev)
                best_for[v] = idx
            else:
                drop.add(idx)
    if not drop:
        return df, 0
    return df.loc[~df.index.isin(drop)], len(drop)


# ════════════════════════════════════════════════════════════════════════
#  FAZ 3 — Multi-stage matching
# ════════════════════════════════════════════════════════════════════════

_DOI_SEP_CHARS = frozenset("-._")


def _doi_sep_profile(suffix: str) -> tuple[str, dict[int, str]]:
    """DOI son ekini (ayırıcısız çekirdek, konum→ayırıcı) ikilisine ayır."""
    core: list[str] = []
    gaps: dict[int, str] = {}
    for ch in suffix:
        if ch in _DOI_SEP_CHARS:
            gaps[len(core)] = gaps.get(len(core), "") + ch
        else:
            core.append(ch)
    return "".join(core), gaps


def doi_separator_variants(canon_a: Optional[str], canon_b: Optional[str]) -> bool:
    """İki KANONİK DOI yalnızca ayırıcı yazımında mı farklı?

    DOI BELİRLEYİCİ kuralı yerinde kalır: normalize DOI'leri farklı olan kayıtlar
    farklı yayındır. Tek istisna, dizinleyicinin aynı DOI'yi farklı yazmasıdır —
    '10.7189/jogh.12-05057' ile '10.7189/JOGH.12.05057' aynı makaledir. Böyle bir
    çiftte veto kalkar; ama DOI KANIT SAYILMAZ, çift kanıtını başlık+yıl+yazar
    aşamalarından almak zorundadır (asla Stage 1 üretmez).

    Kural KASITLI olarak dar. Ayırıcıların hepsini silip karşılaştırmak (iskelet
    yaklaşımı) gerçek ve FARKLI yayınları çakıştırıyor: ölçümde Physical Review'da
    cilt|sayfa sınırının kaydığı 34 doğrulanmış çift bulundu — '…/physrevc.5.350'
    (Michaud 1972, cilt 5, s.350) ile '…/physrevc.53.50' (Awasthi 1996, cilt 53,
    s.50) ikisi de kayıtlı, apayrı makaleler. Bu yüzden:
      • registrant öneki (ilk '/' öncesi) birebir aynı olmalı,
      • ayırıcı olmayan çekirdek birebir aynı olmalı,
      • farklılaşan her ayırıcı konumu ya bire-bir ikame (- ↔ . ↔ _) olmalı,
        ya da en az bir komşusu rakam OLMAYAN iç sınırda ekleme/silme olmalı.
    İki rakam arasında ayırıcı ekleme/silme, kenarda ekleme/silme ve '--' ↔ '-'
    REDDEDİLİR. Ölçüm: 34 farklı-yayın çiftinin 34'ü reddedilir, 10 gerçek
    yazım-farkı çiftinin 10'u kabul edilir, 32.454 adayda yanlış pozitif 0.
    """
    if not canon_a or not canon_b or canon_a == canon_b:
        return False
    pa, _, sa = canon_a.partition("/")
    pb, _, sb = canon_b.partition("/")
    if pa != pb or not sa or not sb:
        return False
    ca, ga = _doi_sep_profile(sa)
    cb, gb = _doi_sep_profile(sb)
    if ca != cb:
        return False
    for i in set(ga) | set(gb):
        x, y = ga.get(i, ""), gb.get(i, "")
        if x == y:
            continue
        if len(x) == 1 and len(y) == 1:
            continue                      # bire-bir ikame: güvenli
        if len(x) > 1 or len(y) > 1:
            return False                  # '--' ↔ '-'
        if i == 0 or i == len(ca):
            return False                  # kenarda ekleme/silme
        if ca[i - 1].isdigit() and ca[i].isdigit():
            return False                  # rakam|rakam: bölümleme kayması
    return True


def doi_conflict(raw_a: Any, raw_b: Any) -> bool:
    """İki ham DOI normalize edilince ikisi de mevcut ve FARKLI mı?

    DOI BELİRLEYİCİDİR: arındırılmış (normalize) DOI'ler farklıysa kayıtlar ASLA
    aynı yayın değildir — borderline (manuel onay) listesinde bile gösterilmezler.
    Bir tarafta DOI yoksa çelişki yoktur (False). `negative_rule_check` yeni
    merge'lerde bu çiftleri zaten eler; bu yardımcı, eski kuyrukları okurken
    (`list_borderline`) geriye dönük güvenlik katmanı sağlar.
    """
    a = normalize_doi(raw_a)
    b = normalize_doi(raw_b)
    if not a or not b or a == b:
        return False
    # Yalnız ayırıcı yazımı farklıysa bu bir çelişki değil; çift manuel
    # incelemeye (borderline) girebilsin diye veto uygulanmaz.
    return not doi_separator_variants(a, b)


def compute_match(w: dict, s: dict) -> Optional[dict]:
    """5-aşamalı eşleştirme kararı.

    Dönüş: None (no match) veya {stage, confidence, reason, jw_title, year_diff, surname_match}
    """
    # ── Kimlik HİYERARŞİSİ: DOI > PMID > ISSN ─────────────────────────────
    # Bir üst seviyedeki KESİN eşitlik, alt seviyedeki çelişkiyi geçersiz kılar.
    # Örn. WoS print-ISSN, Scopus e-ISSN verir — aynı DOI'li çiftte ISSN
    # çelişkisi vetolayamaz (eski davranış: negative_rule_check tüm seviyeleri
    # birden uygulayıp aynı-DOI çifti sessizce duplike bırakıyordu). Alt-seviye
    # çelişki yalnız üst seviyede eşitlik YOKKEN reddeder.

    # DOI: çelişki → asla aynı yayın; eşitlik → kesin merge
    w_doi = w.get("_norm_doi")
    s_doi = s.get("_norm_doi")
    doi_note = ""
    if _present(w_doi) and _present(s_doi) and w_doi != s_doi:
        if not doi_separator_variants(w_doi, s_doi):
            return None
        # Ayırıcı/yazım farkı: veto kalkar, DOI kanıt sayılmaz. Çift aşağıdaki
        # başlık/yıl/yazar aşamalarından geçmek zorunda — Stage 1 üretilmez.
        doi_note = f" | DOI yazım farkı: {w_doi} ↔ {s_doi}"
    elif _present(w_doi) and _present(s_doi):
        return {
            "stage": "1_doi_exact",
            "stage_label": "DOI exact",
            "confidence": 1.00,
            "reason": f"DOI exact: {w_doi}",
            "jw_title": None,
            "year_diff": None,
            "surname_match": None,
        }

    # PMID (DOI karşılaştırılamadıysa): çelişki → reject; eşitlik → merge
    # (UT cross-database aynı değildir, sadece PMID)
    w_pmid = w.get("_norm_pmid")
    s_pmid = s.get("_norm_pmid")
    if _present(w_pmid) and _present(s_pmid):
        if w_pmid != s_pmid:
            return None
        return {
            "stage": "2_pmid_exact",
            "stage_label": "PMID exact",
            "confidence": 0.99,
            "reason": f"PMID exact: {w_pmid}",
            "jw_title": None,
            "year_diff": None,
            "surname_match": None,
        }

    # ISSN (dergi-seviyesi koruma): yalnız başlık aşamalarını veto eder
    w_issn = w.get("_norm_issn")
    s_issn = s.get("_norm_issn")
    if _present(w_issn) and _present(s_issn) and w_issn != s_issn:
        return None

    # Stage 3 — Title JW ≥ 0.92 + Year ±1 + Surname match
    w_title = w.get("_norm_title", "")
    s_title = s.get("_norm_title", "")
    if w_title and s_title:
        jw_title = jaro_winkler(w_title, s_title)
        w_year = w.get("_norm_year")
        s_year = s.get("_norm_year")
        year_diff = abs((w_year or 0) - (s_year or 0)) if (w_year is not None and s_year is not None) else None
        w_surname = w.get("_norm_surname", "")
        s_surname = s.get("_norm_surname", "")
        surname_match = bool(w_surname and s_surname and w_surname == s_surname)

        # Jenerik/kısa başlık koruması: "Editorial", "Erratum", "Preface" gibi
        # başlıklar aynı yazar+yılda BİRDEN ÇOK farklı yayına ait olabilir
        # (derginin editörü her sayıya bir editorial yazar). Bu tür başlıklar
        # otomatik Stage 3'e giremez; yalnız kimlik (DOI/PMID) veya Stage 4
        # (dergi+cilt+sayfa) ile birleşir.
        informative_title = len(w_title.split()) >= 3 or len(w_title) >= 15

        if (
            informative_title
            and jw_title >= TITLE_EXACT_THRESHOLD
            and year_diff is not None
            and year_diff <= YEAR_TOLERANCE
            and surname_match
        ):
            return {
                "stage": "3_title_year_surname",
                "stage_label": "Title+Year+Surname",
                "confidence": 0.95,
                "reason": f"JW(title)={jw_title:.3f} ≥ {TITLE_EXACT_THRESHOLD}, year_diff={year_diff}, surname='{w_surname}' eşleşti" + doi_note,
                "jw_title": round(jw_title, 4),
                "year_diff": year_diff,
                "surname_match": surname_match,
            }

        # Stage 4 — Journal + Volume + (Pages veya BP) — DOI'siz eski yayınlar için
        w_journal = w.get("_norm_journal", "")
        s_journal = s.get("_norm_journal", "")
        if w_journal and s_journal:
            jw_journal = jaro_winkler(w_journal, s_journal)
            # xlsx yolu sayısal alanları float yapar ("100.0"); WoS txt string
            # ("100"). ".0" kuyruğu temizlenmeden karşılaştırma hep ıskalardı.
            w_vol = _clean_num(w.get("VL", ""))
            s_vol = _clean_num(s.get("VL", ""))
            w_bp = _clean_num(w.get("BP", ""))
            s_bp = _clean_num(s.get("BP", ""))
            w_pg = _clean_num(w.get("PG", ""))
            s_pg = _clean_num(s.get("PG", ""))
            page_match = (w_bp and s_bp and w_bp == s_bp) or (w_pg and s_pg and w_pg == s_pg)
            if (
                jw_journal >= JOURNAL_SIMILARITY
                and w_vol and s_vol and w_vol == s_vol
                and page_match
            ):
                return {
                    "stage": "4_journal_vol_page",
                    "stage_label": "Journal+Vol+Pages",
                    "confidence": 0.90,
                    "reason": f"JW(journal)={jw_journal:.3f}, vol={w_vol}, page_match=True" + doi_note,
                    "jw_title": round(jw_title, 4),
                    "year_diff": year_diff,
                    "surname_match": surname_match,
                }

        # Stage 5 — Borderline (manual queue)
        if TITLE_BORDERLINE_LOW <= jw_title < TITLE_EXACT_THRESHOLD:
            # Confidence linear scale 0.70-0.85
            conf = 0.70 + (jw_title - TITLE_BORDERLINE_LOW) * (0.85 - 0.70) / (TITLE_EXACT_THRESHOLD - TITLE_BORDERLINE_LOW)
            return {
                "stage": "5_borderline",
                "stage_label": "Borderline (manual review)",
                "confidence": round(conf, 3),
                "reason": f"JW(title)={jw_title:.3f}, year_diff={year_diff}, surname_match={surname_match}" + doi_note,
                "jw_title": round(jw_title, 4),
                "year_diff": year_diff,
                "surname_match": surname_match,
            }

    return None


def generate_candidates(
    wos_df: pd.DataFrame, scp_df: pd.DataFrame,
) -> list[tuple[float, int, int, dict]]:
    """Aday (WoS, Scopus) çiftlerini üret — KİMLİK-ÖNCELİKLİ + blocking.

    Kritik kural: aynı normalize DOI'yi (veya PMID'yi) paylaşan her çift,
    blocking'e BAKILMAKSIZIN değerlendirilir. Eski davranışta adaylar yalnız
    (yıl, soyad ilk harfi) bloklarından geliyordu; WoS ile Scopus soyadı farklı
    ayrıştırınca (örn. 'RAHIM ZA' vs 'ABDUL RAHIM NR') veya yıl farklıysa
    (early-access 2024 vs basılı 2025) aynı DOI'li çift HİÇ karşılaştırılmıyor,
    DOI-exact aşamasına sıra gelmiyordu → duplike kayıtlar dataset'te kalıyordu.
    Blocking artık yalnız başlık-benzerliği aşamaları için aday sınırlar.

    Dönüş: confidence azalan sırada [(confidence, w_idx, s_idx, match), ...]
    """
    candidates: list[tuple[float, int, int, dict]] = []
    seen_pairs: set[tuple[int, int]] = set()

    def consider(w_idx: int, s_idx: int) -> None:
        pair = (int(w_idx), int(s_idx))
        if pair in seen_pairs:
            return
        seen_pairs.add(pair)
        m = compute_match(wos_df.loc[w_idx].to_dict(), scp_df.loc[s_idx].to_dict())
        if m is not None:
            candidates.append((m["confidence"], pair[0], pair[1], m))

    # 1) Kimlik indeksleri — DOI ve PMID paylaşan çiftler blok bağımsız
    for key_col in ("_norm_doi", "_norm_pmid"):
        if key_col not in wos_df.columns or key_col not in scp_df.columns:
            continue
        w_index: dict[str, list[int]] = {}
        for idx in wos_df.index:
            v = wos_df.at[idx, key_col]
            if _present(v):
                w_index.setdefault(v, []).append(int(idx))
        for idx in scp_df.index:
            v = scp_df.at[idx, key_col]
            if not _present(v):
                continue
            for w_idx in w_index.get(v, ()):
                consider(w_idx, int(idx))

    # 2) Blocking — başlık-benzerliği aşamaları için aday uzayı
    # Blok anahtarı (yıl, soyad ilk harfi) BİREBİR yıl istediği için Stage 3'ün
    # ±1 yıl toleransı pratikte hiç devreye girmiyordu: erken-erişim/basılı yıl
    # farkı olan gerçek çiftler aday bile olmuyordu. WoS satırı artık komşu
    # yılların Scopus bloklarıyla da karşılaştırılır (TEK taraflı: aday sayısı
    # ~3× artar, 9× değil). Yılı bilinmeyen kayıtlar eski davranışta kalır —
    # None'ı her yıla açmak aday uzayını patlatır.
    wos_blocks = build_blocks(wos_df)
    scp_blocks = build_blocks(scp_df)
    for (year, letter), w_idxs in wos_blocks.items():
        years = (year,) if year is None else (year - 1, year, year + 1)
        for y in years:
            s_idxs = scp_blocks.get((y, letter))
            if not s_idxs:
                continue
            for w_idx in w_idxs:
                for s_idx in s_idxs:
                    consider(w_idx, s_idx)

    candidates.sort(key=lambda x: -x[0])
    return candidates


# ════════════════════════════════════════════════════════════════════════
#  FAZ 4 — Field merge with Caputo 2024 preferences
# ════════════════════════════════════════════════════════════════════════

def _is_empty(v: Any) -> bool:
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s == "" or s.lower() == "nan"


def _union_values(w_val: Any, s_val: Any, sep: str = "; ") -> str:
    """Iki değeri ; ile birleştirip dedup et (case-insensitive)."""
    parts: list[str] = []
    seen: set[str] = set()
    for v in (w_val, s_val):
        if _is_empty(v):
            continue
        for token in re.split(r"\s*[;|]\s*", _to_str(v)):
            token = token.strip()
            if not token:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            parts.append(token)
    return sep.join(parts)


def _apply_preference(field: str, w_val: Any, s_val: Any) -> tuple[Any, str]:
    """Bir alan için Caputo 2024 default tercihini uygula.

    Dönüş: (chosen_value, chosen_source) — source in {"wos","scopus","union","cross_fill","empty"}
    """
    pref = FIELD_PREFERENCES.get(field, DEFAULT_PREFERENCE)
    w_empty = _is_empty(w_val)
    s_empty = _is_empty(s_val)

    if w_empty and s_empty:
        return "", "empty"

    if pref == "wos":
        return (w_val, "wos") if not w_empty else (s_val, "scopus_fallback")
    if pref == "scopus":
        return (s_val, "scopus") if not s_empty else (w_val, "wos_fallback")
    if pref == "union":
        return _union_values(w_val, s_val), "union"
    if pref == "cross_fill_wos_first":
        if not w_empty:
            return w_val, "wos"
        return s_val, "scopus"
    if pref == "wos_first":
        if not w_empty:
            return w_val, "wos"
        return s_val, "scopus"
    # Bilinmeyen pref — default wos_first
    if not w_empty:
        return w_val, "wos"
    return s_val, "scopus"


def merge_pair_with_preferences(
    pair_id: str,
    w: dict,
    s: dict,
    all_columns: list[str],
) -> tuple[dict, list[dict]]:
    """İki kaydı birleştir, çakışmaları logla.

    Dönüş: (merged_row, conflicts_list)
    """
    merged: dict[str, Any] = {}
    conflicts: list[dict] = []

    for col in all_columns:
        if col.startswith("_norm_"):
            continue  # internal kolonlar
        w_val = w.get(col)
        s_val = s.get(col)
        chosen, source = _apply_preference(col, w_val, s_val)
        merged[col] = chosen

        # Çakışma logu: iki taraf da dolu VE değerler farklı
        if not _is_empty(w_val) and not _is_empty(s_val):
            if _to_str(w_val).lower().strip() != _to_str(s_val).lower().strip():
                conflicts.append({
                    "pair_id": pair_id,
                    "field": col,
                    "wos_value": _to_str(w_val)[:200],
                    "scopus_value": _to_str(s_val)[:200],
                    "chosen_source": source,
                    "chosen_value": _to_str(chosen)[:200],
                    "preference_rule": FIELD_PREFERENCES.get(col, DEFAULT_PREFERENCE),
                })

    # DB etiketleme — birleştirilmiş kayıt
    merged["DB"] = "BIBEXPY_SMART"
    merged["DB_Original"] = "ISI; SCOPUS"

    return merged, conflicts


# ════════════════════════════════════════════════════════════════════════
#  FAZ 5 — Yazıcılar (audit, conflict, borderline, lost, statistic)
# ════════════════════════════════════════════════════════════════════════

def _write_match_audit(rows: list[dict], out: Path) -> None:
    if not rows:
        return
    df = pd.DataFrame(rows)
    cols = ["pair_id", "wos_index", "scp_index", "doi", "stage", "stage_label",
            "confidence", "jw_title", "year_diff", "surname_match", "reason"]
    cols = [c for c in cols if c in df.columns]
    df[cols].to_excel(out, index=False)


def _write_conflict_log(conflicts: list[dict], out: Path) -> None:
    if not conflicts:
        return
    df = pd.DataFrame(conflicts)
    df.to_excel(out, index=False)


def _write_borderline_queue(items: list[dict], out: Path) -> None:
    if not items:
        return
    df = pd.DataFrame(items)
    df.to_excel(out, index=False)


def _write_lost_records(df: pd.DataFrame, out: Path) -> None:
    if df.empty:
        return
    # Internal _norm_* kolonlarını çıkar
    cols = [c for c in df.columns if not c.startswith("_norm_")]
    df[cols].to_excel(out, index=False)


def _write_statistic_smart(
    total: int, wos_count: int, scp_count: int,
    merged_df: pd.DataFrame, out: Path,
) -> None:
    """Mevcut Statistic.xlsx şemasıyla uyumlu — General Stats + Field Stats sheet'leri."""
    general = pd.DataFrame([{
        "Total Records": total,
        "WoS Records": wos_count,
        "Scopus Records": scp_count,
        "Merged Columns": len(merged_df.columns),
        "Common Columns": 0,  # placeholder
    }])

    # Field stats
    field_rows = []
    for col in merged_df.columns:
        if col.startswith("_norm_"):
            continue
        missing = int(merged_df[col].apply(_is_empty).sum())
        pct = (missing / total * 100) if total else 0
        if pct == 0:
            status = "Excellent"
        elif pct < 5:
            status = "Very Good"
        elif pct < 15:
            status = "Good"
        elif pct < 40:
            status = "Poor"
        else:
            status = "Very Poor"
        field_rows.append({
            "": col,
            "Description": col,
            "Missing Count": missing,
            "Missing %": round(pct, 2),
            "Status": status,
        })
    fields = pd.DataFrame(field_rows)

    # Atomik yazım: merge özeti (GET /merge/summary) bu dosyayı okurken yarım
    # yazılmış bir dosya görürse sessizce yedek yola düşüp WoS/Scopus=0 gösterir.
    tmp = out.with_name(out.name + f".{uuid4().hex[:8]}.tmp~")
    try:
        with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
            general.to_excel(writer, sheet_name="General Stats", index=False)
            fields.to_excel(writer, sheet_name="Field Stats", index=False)
        tmp.replace(out)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _refresh_statistic_after_borderline(adir: Path, df: pd.DataFrame) -> None:
    """Borderline kabulünden sonra Statistic.xlsx'i dataset'in gerçek haliyle yaz.

    Merge özeti ve yöntem paragrafı sayılarını Statistic.xlsx'ten okur; dosya
    yalnız merge anında yazıldığı için kabul edilen çiftler hiç yansımıyordu
    (dataset 1.292 kayda inse de metin 1.296 diyordu). Girdi sayıları (WoS /
    Scopus) değişmez; toplam ve alan doluluk istatistikleri yeniden hesaplanır.
    """
    stat = adir / "Statistic.xlsx"
    if not stat.exists():
        legacy = adir / "Statistic_Smart.xlsx"
        if not legacy.exists():
            return
        stat = legacy
    gen = pd.read_excel(stat, sheet_name="General Stats")
    if gen.empty:
        return
    row = gen.iloc[0]
    wos_n = int(row.get("WoS Records", 0) or 0)
    scp_n = int(row.get("Scopus Records", 0) or 0)
    _write_statistic_smart(int(len(df)), wos_n, scp_n, df, stat)


def _append_conflict_log(path: Path, conflicts: list[dict]) -> int:
    """Kabul edilen çiftlerin alan çakışmalarını conflict_log.xlsx'e ekle.

    Aynı pair_id'nin eski satırları atılır (tekrar kabul idempotent kalsın).
    Atomik yazılır; hata dataset kararını geri almaz — çağıran yakalar.
    """
    if not conflicts:
        return 0
    new = pd.DataFrame(conflicts)
    if path.exists():
        old = pd.read_excel(path)
        if "pair_id" in old.columns:
            old = old[~old["pair_id"].astype(str).isin(set(new["pair_id"].astype(str)))]
        new = pd.concat([old, new], ignore_index=True)
    tmp = path.with_name(path.name + f".{uuid4().hex[:8]}.tmp~")
    try:
        new.to_excel(tmp, index=False)
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return len(conflicts)


# ════════════════════════════════════════════════════════════════════════
#  Borderline state yönetimi (JSON)
# ════════════════════════════════════════════════════════════════════════

def _borderline_state_path(project_id: str, adir: Optional[Path] = None) -> Optional[Path]:
    """Borderline state dosyasının yolu — aktif analiz klasöründe."""
    if adir is None:
        adir = analyses.get_active_analysis_dir(project_id)
    if adir is None:
        return None
    return adir / "borderline_state.json"


def _read_borderline_state(project_id: str, adir: Optional[Path] = None) -> dict[str, dict]:
    p = _borderline_state_path(project_id, adir)
    if p is None or not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_borderline_state(project_id: str, state: dict[str, dict], adir: Optional[Path] = None) -> None:
    p = _borderline_state_path(project_id, adir)
    if p is None:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ════════════════════════════════════════════════════════════════════════
#  ANA ORKESTRATÖR
# ════════════════════════════════════════════════════════════════════════

async def run_smart_merge(ctx: JobContext, project_id: str) -> dict[str, Any]:
    """Smart Merge ana iş akışı. Result dict döner (audit hook için).

    Yeni bir analiz klasörü oluşturur, çıktıları oraya yazar ve sonunda
    aktif analiz olarak işaretler. Hata durumunda yarım klasör temizlenir.
    """

    ctx.log("Starting Smart Merge...")
    ctx.progress(0.02)

    # 0. Yeni analiz klasörü
    analysis_id, adir = analyses.create_analysis(project_id, "smart")
    ctx.log(f"Analysis folder: {analysis_id}")

    # 1. Veriyi yükle (merger.py'dan reuse)
    from services.merger import _load_inputs

    # Büyük konsolide workbook'ların okunması dakikalar sürebilir ve bu sırada
    # başka ilerleme yayını yok — kullanıcı donma sanmasın.
    ctx.log("Loading consolidated sources — large workbooks can take a few minutes...")
    try:
        scp_df, wos_df = await asyncio.to_thread(_load_inputs, project_id, ctx)
    except Exception:
        try:
            analyses.delete_analysis(project_id, analysis_id)
        except Exception:
            pass
        raise
    ctx.log(f"Data loaded: {len(wos_df)} WoS + {len(scp_df)} Scopus")
    ctx.progress(0.10)

    # DB etiketle (_load_inputs taze frame döndürür — .copy() burada yalnızca
    # korpusun bellekte fazladan bir kopyasını yaratıyordu)
    wos_df["DB"] = "ISI"
    scp_df["DB"] = "SCOPUS"

    # NOT: Aşağıdaki CPU-ağır aşamalar (normalize / dedup / aday üretimi /
    # eşleştirme / alan birleştirme) run_cpu havuzunda koşar. Event loop'ta
    # inline koşmaları, büyük korpuslarda loop'u dakikalarca donduruyordu:
    # SSE ping'leri (15 sn) gönderilemiyor, boşta kalan izleme bağlantısı
    # ERR_CONNECTION_RESET ile kopuyor, uygulamanın geri kalanı yanıtsızlaşıyordu.

    # 2. Normalize — yan kolonlar
    ctx.log(f"Normalization: {len(wos_df) + len(scp_df)} rows")

    def _stage_normalize() -> None:
        # Kolonlar LİSTEDEN ve dtype=object olarak kurulur — `.apply()` DEĞİL.
        # pandas 3'te metin sonuçlu apply() `str` dtype üretir ve normalize
        # fonksiyonlarının "boş" demek olan None dönüşlerini NaN'a çevirir.
        # NaN truthy olduğundan `if not v` ile korunan her kimlik kontrolü boş
        # DOI'yi geçerli kimlik sanar: aynı kaynaktaki DOI'siz kayıtların hepsi
        # tek NaN anahtarında toplanıp bire iner (SESSİZ VERİ KAYBI) ve DOI'siz
        # kayıtlar hiçbir şeyle eşleşemez. `astype(object)` bunu DÜZELTMEZ —
        # NaN, object dtype içinde de NaN kalır ve hâlâ truthy'dir.
        def _norm_col(frame: pd.DataFrame, src: str, fn) -> pd.Series:
            raw = frame[src] if src in frame.columns else pd.Series([""] * len(frame), index=frame.index)
            return pd.Series([fn(v) for v in raw], index=frame.index, dtype=object)

        for df in (wos_df, scp_df):
            df["_norm_doi"] = _norm_col(df, "DI", normalize_doi)
            df["_norm_title"] = _norm_col(df, "TI", normalize_title)
            df["_norm_year"] = _norm_col(df, "PY", normalize_year)
            df["_norm_surname"] = _norm_col(df, "AU", normalize_author_surname)
            df["_norm_issn"] = _norm_col(df, "SN", normalize_issn)
            df["_norm_pmid"] = _norm_col(df, "PM", normalize_id_token)
            df["_norm_ut"] = _norm_col(df, "UT", normalize_id_token)
            df["_norm_journal"] = _norm_col(df, "SO", normalize_title)
            # Invariant — sessiz kayıp yerine gürültülü hata: kimlik kolonları
            # object dtype olmazsa (ileride biri .apply()'a dönerse) boş kimlik
            # yeniden NaN olur ve veri kaybı sessizce geri gelir.
            for _col in ("_norm_doi", "_norm_pmid", "_norm_ut"):
                if df[_col].dtype != object:
                    raise RuntimeError(
                        f"normalize invariant ihlali: {_col} dtype={df[_col].dtype} "
                        "(object bekleniyor — boş kimlikler None kalmalı)"
                    )

    await run_cpu(_stage_normalize)
    ctx.progress(0.20)

    # 2b. Kaynak-İÇİ dedup — aynı UT/EID veya aynı DOI'li satırlardan en dolusu
    # kalır (üst üste binen export dosyaları; cross-source eşleşme bunları görmez).
    wos_raw_n, scp_raw_n = len(wos_df), len(scp_df)
    (wos_df, intra_wos_removed), (scp_df, intra_scp_removed) = await run_cpu(
        lambda: (dedup_within_source(wos_df), dedup_within_source(scp_df))
    )
    if intra_wos_removed or intra_scp_removed:
        ctx.log(f"Intra-source dedup: {intra_wos_removed} WoS + {intra_scp_removed} Scopus duplicate rows removed")
    ctx.progress(0.25)

    # 3-4. Aday üretimi (KİMLİK-ÖNCELİKLİ: aynı DOI/PMID'li çiftler blocking'e
    # bakılmaksızın değerlendirilir; blocking yalnız başlık aşamalarını sınırlar)
    # + greedy one-to-one atama.
    matches: list[dict] = []           # match_audit rows
    borderline: list[dict] = []        # borderline_queue rows
    matched_wos: set[int] = set()
    matched_scp: set[int] = set()

    # Sayaçlar
    stage_counts: dict[str, int] = {}
    pair_counter = 0

    candidates = await run_cpu(generate_candidates, wos_df, scp_df)
    ctx.log(f"Candidate pairs: {len(candidates)}")
    ctx.progress(0.40)

    # Greedy assignment + borderline ayır (CPU havuzunda — aday sayısı büyük
    # korpuslarda yüz binleri bulabilir)
    def _stage_assign() -> int:
        pair_counter = 0
        for conf, w_idx, s_idx, m in candidates:
            if w_idx in matched_wos or s_idx in matched_scp:
                continue

            pair_counter += 1
            pair_id = f"p{pair_counter:06d}"

            if m["stage"] == "5_borderline":
                # Borderline — UI'da manuel onay bekleyecek
                w_row = wos_df.loc[w_idx]
                s_row = scp_df.loc[s_idx]
                borderline.append({
                    "pair_id": pair_id,
                    "wos_index": int(w_idx),
                    "scp_index": int(s_idx),
                    "jw_title": m["jw_title"],
                    "year_diff": m["year_diff"],
                    "surname_match": m["surname_match"],
                    "confidence": m["confidence"],
                    "stage_label": m["stage_label"],
                    "reason": m["reason"],
                    "wos_doi": _to_str(w_row.get("DI", "")),
                    "scp_doi": _to_str(s_row.get("DI", "")),
                    # Kimlikler: kabul edilen çiftin satırını DOI'ye bakmadan
                    # bulmak için (DOI'siz çiftlerde kabul sessizce hiçbir şey
                    # yapmıyordu). Scopus EID'si UT kolonunda taşınır.
                    "wos_ut": _to_str(w_row.get("UT", "")),
                    "scp_ut": _to_str(s_row.get("UT", "")),
                    "wos_title": _to_str(w_row.get("TI", ""))[:200],
                    "scp_title": _to_str(s_row.get("TI", ""))[:200],
                    "wos_year": w_row.get("_norm_year"),
                    "scp_year": s_row.get("_norm_year"),
                    "wos_surname": _to_str(w_row.get("_norm_surname", "")),
                    "scp_surname": _to_str(s_row.get("_norm_surname", "")),
                    "wos_journal": _to_str(w_row.get("SO", "")),
                    "scp_journal": _to_str(s_row.get("SO", "")),
                    "wos_volume": _to_str(w_row.get("VL", "")),
                    "scp_volume": _to_str(s_row.get("VL", "")),
                    "status": "pending",
                })
            else:
                # Definite match — birleştir
                matched_wos.add(w_idx)
                matched_scp.add(s_idx)
                stage_counts[m["stage_label"]] = stage_counts.get(m["stage_label"], 0) + 1
                matches.append({
                    "pair_id": pair_id,
                    "wos_index": int(w_idx),
                    "scp_index": int(s_idx),
                    "doi": m["reason"].split("DOI exact: ", 1)[-1].split(",")[0] if "DOI exact" in m["reason"] else "",
                    "stage": m["stage"],
                    "stage_label": m["stage_label"],
                    "confidence": m["confidence"],
                    "jw_title": m.get("jw_title"),
                    "year_diff": m.get("year_diff"),
                    "surname_match": m.get("surname_match"),
                    "reason": m["reason"],
                })
        return pair_counter

    pair_counter = await run_cpu(_stage_assign)
    ctx.log(f"Matches: {len(matches)} exact, {len(borderline)} borderline")
    for stage_label, n in stage_counts.items():
        ctx.log(f"  Stage [{stage_label}]: {n}")
    ctx.progress(0.65)

    # 5-7. Field merge + eşleşmeyenler + tek DataFrame + UID (CPU havuzunda)
    ctx.log("Field merge (Caputo 2024 defaults)...")

    cr_stats: dict[str, int] = {"normalized": 0, "nr_filled": 0}

    def _stage_field_merge():
        all_columns = list(set(list(wos_df.columns) + list(scp_df.columns)))
        conflicts: list[dict] = []
        merged_rows: list[dict] = []
        field_source_distribution: dict[str, int] = {}

        for match in matches:
            w_idx = match["wos_index"]
            s_idx = match["scp_index"]
            w_row = wos_df.loc[w_idx].to_dict()
            s_row = scp_df.loc[s_idx].to_dict()
            merged_row, pair_conflicts = merge_pair_with_preferences(
                match["pair_id"], w_row, s_row, all_columns
            )
            merged_rows.append(merged_row)
            conflicts.extend(pair_conflicts)
            for c in pair_conflicts:
                src = c["chosen_source"]
                field_source_distribution[src] = field_source_distribution.get(src, 0) + 1

        # 6. Eşleşmeyen WoS / Scopus satırları → ana df'ye olduğu gibi eklenir
        wos_not_matched = wos_df.loc[~wos_df.index.isin(matched_wos)].copy()
        scp_not_matched = scp_df.loc[~scp_df.index.isin(matched_scp)].copy()
        # _norm_* kolonlarını çıkar
        for df in (wos_not_matched, scp_not_matched):
            drop_cols = [c for c in df.columns if c.startswith("_norm_")]
            df.drop(columns=drop_cols, inplace=True, errors="ignore")

        merged_df = pd.DataFrame(merged_rows)
        # _norm_* drop
        drop_cols = [c for c in merged_df.columns if c.startswith("_norm_")]
        if drop_cols:
            merged_df.drop(columns=drop_cols, inplace=True, errors="ignore")

        # Tek bir DataFrame + UID kolonu (filter_engine ile aynı şema)
        final_df = pd.concat([merged_df, wos_not_matched, scp_not_matched], ignore_index=True)
        filter_engine._ensure_uid_column(final_df)

        # SAYI KORUNUMU (lineage invariant): çıktı = (kaynak − kaynak-içi
        # duplike) − eşleşen çift. Sapma, sessizce kayıt kaybı ya da
        # duplikasyon demektir; pandas 3 NaN hatası tam olarak böyle, hiçbir
        # uyarı vermeden 70 yayın kaybettirmişti. Bozuk bir dataset yazmaktansa
        # koşuyu burada düşürmek doğrudur.
        _expected = ((wos_raw_n - intra_wos_removed)
                     + (scp_raw_n - intra_scp_removed)
                     - len(matches))
        if len(final_df) != _expected:
            raise RuntimeError(
                f"lineage invariant ihlali: {_expected} kayıt bekleniyordu "
                f"(WoS {wos_raw_n}−{intra_wos_removed}, "
                f"Scopus {scp_raw_n}−{intra_scp_removed}, eşleşme {len(matches)}), "
                f"üretilen {len(final_df)}"
            )

        # CR normalizasyonu — Scopus dilbilgisindeki atıflar WoS dilbilgisine
        # çevrilir. Aksi hâlde VOSviewer/bibliometrix birleştirilmiş dataset'in
        # Scopus kaynaklı satırlarında hiçbir atıfı eşleştiremez.
        # normalize_cr idempotenttir; WoS satırları olduğu gibi geri döner.
        if "CR" in final_df.columns:
            before = final_df["CR"].fillna("").astype(str)
            after = before.map(normalize_cr)
            # Yalnız dolu hücreler yazılır — boş/NaN CR hücreleri olduğu gibi kalır.
            filled_cr = before.str.strip() != ""
            changed = filled_cr & (after != before)
            cr_stats["normalized"] = int(changed.sum())
            if changed.any():
                final_df["CR"] = final_df["CR"].astype(object)
                final_df.loc[changed, "CR"] = after[changed]
            # NR (referans sayısı): boşsa VEYA CR bu adımda yeniden yazıldıysa
            # CR'den say. Scopus dosyalarında NR kolonu yoktur; ayrıca
            # scopus_fallback ile CR'ı Scopus'tan gelen eşleşmiş satırlarda WoS
            # tarafının NR'ı (çoğu kez 0) artık dizgeyi tarif etmez.
            if "NR" not in final_df.columns:
                final_df["NR"] = ""
            nr = final_df["NR"]
            blank = nr.isna() | nr.astype(str).str.strip().isin(("", "nan", "NaN", "None"))
            fill = (blank | changed) & filled_cr
            if fill.any():
                # object'e çevir: int64/float64 kolona tamsayı yazmak 3 yerine
                # 3.0 üretir ve WoS dosyasına "NR 3.0" olarak düşer.
                final_df["NR"] = final_df["NR"].astype(object)
                final_df.loc[fill, "NR"] = after[fill].map(count_refs)
                cr_stats["nr_filled"] = int(fill.sum())

        return final_df, conflicts, field_source_distribution, wos_not_matched, scp_not_matched

    (final_df, conflicts, field_source_distribution,
     wos_not_matched, scp_not_matched) = await run_cpu(_stage_field_merge)
    ctx.log(f"CR normalization (Scopus → WoS grammar): {cr_stats['normalized']} cells rewritten, "
            f"{cr_stats['nr_filled']} NR values filled")
    ctx.progress(0.80)

    # 8. Çıktıları yaz — analiz klasörüne
    ctx.log("Writing output files...")
    # Ana dataset parquet (dahili çalışma formatı); yan inceleme dosyaları xlsx kalır.
    merged_dataset = adir / "merged.parquet"
    audit_xlsx = adir / "match_audit.xlsx"
    conflict_xlsx = adir / "conflict_log.xlsx"
    borderline_xlsx = adir / "borderline_queue.xlsx"
    lost_wos_xlsx = adir / "Lost_Wos_Records.xlsx"
    lost_scp_xlsx = adir / "Lost_Scopus_Records.xlsx"
    stat_xlsx = adir / "Statistic.xlsx"

    try:
        await asyncio.to_thread(dataset_io.atomic_write_dataset, final_df, merged_dataset)
        await asyncio.to_thread(_write_match_audit, matches, audit_xlsx)
        await asyncio.to_thread(_write_conflict_log, conflicts, conflict_xlsx)
        await asyncio.to_thread(_write_borderline_queue, borderline, borderline_xlsx)
        await asyncio.to_thread(_write_lost_records, wos_not_matched, lost_wos_xlsx)
        await asyncio.to_thread(_write_lost_records, scp_not_matched, lost_scp_xlsx)
        await asyncio.to_thread(
            _write_statistic_smart,
            len(final_df), wos_raw_n, scp_raw_n, final_df, stat_xlsx,
        )
    except Exception:
        # Yazma sırasında hata — yarım analiz klasörünü temizle
        try:
            analyses.delete_analysis(project_id, analysis_id)
        except Exception:
            pass
        raise

    # 9. Borderline state'i kaydet (analiz klasörü içine)
    state = {b["pair_id"]: {"status": "pending", "decided_at": None} for b in borderline}
    if borderline:
        _write_borderline_state(project_id, state, adir=adir)

    # 10. Filter cache invalidate (Smart artık aktif olacak)
    filter_engine._DF_CACHE.clear()

    # 11. Finalize — file_count + aktif yap
    analyses.finalize_analysis(project_id, analysis_id)

    ctx.progress(1.0)
    storage.touch_project(project_id)

    # 12. Result özeti (audit hook için)
    summary = {
        "method": "smart",
        "analysis_id": analysis_id,
        "scopus_input": int(scp_raw_n),
        "wos_input": int(wos_raw_n),
        "merged_count": int(len(final_df)),
        "matched_pairs": int(len(matches)),
        "intra_wos_removed": int(intra_wos_removed),
        "intra_scopus_removed": int(intra_scp_removed),
        "borderline_count": int(len(borderline)),
        "borderline_pending": int(len(borderline)),
        "conflict_count": int(len(conflicts)),
        "match_stages": stage_counts,
        "field_source_distribution": field_source_distribution,
        "lost_wos_count": int(len(wos_not_matched)),
        "lost_scopus_count": int(len(scp_not_matched)),
        "output_dataset": str(merged_dataset.relative_to(storage.settings.storage_path)),
        "output_files": [
            f.name for f in (
                merged_dataset, audit_xlsx, conflict_xlsx, borderline_xlsx,
                lost_wos_xlsx, lost_scp_xlsx, stat_xlsx,
            ) if f.exists()
        ],
    }
    ctx.log(f"Smart Merge complete — {summary['merged_count']} unique records "
            f"({summary['matched_pairs']} matched + {summary['borderline_count']} borderline)")
    return summary


# ════════════════════════════════════════════════════════════════════════
#  FAZ 6 — Borderline review
# ════════════════════════════════════════════════════════════════════════

def list_borderline(project_id: str) -> list[dict]:
    """borderline_queue.xlsx + borderline_state.json birleşik liste (aktif analizden)."""
    adir = analyses.get_active_analysis_dir(project_id)
    if adir is None:
        return []
    bq_path = adir / "borderline_queue.xlsx"
    if not bq_path.exists():
        return []
    try:
        df = pd.read_excel(bq_path)
    except Exception:
        return []
    state = _read_borderline_state(project_id, adir=adir)
    items: list[dict] = []
    for _, row in df.iterrows():
        pair_id = str(row.get("pair_id", ""))
        # DOI belirleyici: arındırılmış DOI'ler farklıysa asla aynı yayın değildir.
        # Eski (düzeltme öncesi) kuyruklarda kalmış olsa bile bu çiftleri manuel
        # onaya GÖSTERME — kullanıcıya yalnızca gerçekten belirsiz çiftler sorulur.
        if doi_conflict(row.get("wos_doi"), row.get("scp_doi")):
            continue
        st = state.get(pair_id, {"status": "pending"})
        items.append({
            "pair_id": pair_id,
            "wos_index": int(row.get("wos_index", 0)),
            "scp_index": int(row.get("scp_index", 0)),
            "jw_title": float(row.get("jw_title") or 0),
            "confidence": float(row.get("confidence") or 0),
            "status": st.get("status", "pending"),
            "decided_at": st.get("decided_at"),
            "reason": str(row.get("reason", "")),
            "wos": {
                "doi": str(row.get("wos_doi", "")) or None,
                "title": str(row.get("wos_title", "")),
                "year": int(row["wos_year"]) if pd.notna(row.get("wos_year")) else None,
                "surname": str(row.get("wos_surname", "")) or None,
                "journal": str(row.get("wos_journal", "")) or None,
                "volume": str(row.get("wos_volume", "")) or None,
            },
            "scopus": {
                "doi": str(row.get("scp_doi", "")) or None,
                "title": str(row.get("scp_title", "")),
                "year": int(row["scp_year"]) if pd.notna(row.get("scp_year")) else None,
                "surname": str(row.get("scp_surname", "")) or None,
                "journal": str(row.get("scp_journal", "")) or None,
                "volume": str(row.get("scp_volume", "")) or None,
            },
            "llm_suggestion": st.get("llm_suggestion"),
        })
    return items


def _merge_accepted_pairs(
    df: pd.DataFrame, accept_pairs: list[dict], already: set[str],
) -> tuple[pd.DataFrame, dict[str, str], list[dict]]:
    """Kabul edilen borderline çiftleri GERÇEKTEN birleştir (silme değil).

    Eski davranış yalnızca Scopus satırını siliyordu; böylece kabul edilen her
    çiftte Scopus tercihli alanlar (AB, AU, AF, C1), DE/ID birleşimi ve
    cross-fill'ler kayboluyor, çakışma kaydı hiç yazılmıyordu. Artık merge
    aşamasının kendi kuralları (Caputo tercihleri) uygulanır: kabul edilen çift,
    merge sırasında kesin eşleşme sayılsaydı ne üretecekse onu üretir.

    Satırlar KİMLİKLE bulunur (WoS UT / Scopus EID), bulunamazsa normalize DOI,
    en son normalize başlık+yıl ile; her adımda DB kolonu taraf doğrulaması
    yapar. Belirsiz (birden çok isabet) ya da eksik satır sessizce varsayılmaz:
    çift "unresolved" döner ve kuyrukta bekler.

    Dönüş: (yeni_df, {pair_id: sonuç}, çakışma_kayıtları)
    """
    cols = [c for c in df.columns if c != "UID" and not c.startswith("_norm_")]
    ut_series = df["UT"].map(_to_str).str.lower() if "UT" in df.columns else None
    doi_series = df["DI"].map(normalize_doi) if "DI" in df.columns else None
    title_series = df["TI"].map(normalize_title) if "TI" in df.columns else None
    year_series = df["PY"].map(normalize_year) if "PY" in df.columns else None
    db_series = df["DB"].map(_to_str).str.upper() if "DB" in df.columns else None

    def _side_mask(scopus_side: bool):
        if db_series is None:
            return pd.Series(True, index=df.index)
        return (db_series == "SCOPUS") if scopus_side else (db_series != "SCOPUS")

    def _locate(ut: Any, doi: Any, title: Any, year: Any, scopus_side: bool) -> list:
        side = _side_mask(scopus_side)
        ut_v = _to_str(ut)
        if _present(ut_v) and ut_series is not None:
            hit = list(df.index[(ut_series == ut_v.lower()) & side])
            if hit:
                return hit
        doi_v = normalize_doi(doi)
        if _present(doi_v) and doi_series is not None:
            hit = list(df.index[(doi_series == doi_v) & side])
            if hit:
                return hit
        t = normalize_title(title)
        if t and title_series is not None:
            mask = (title_series == t) & side
            y = normalize_year(year)
            if y is not None and year_series is not None:
                mask = mask & (year_series == y)
            return list(df.index[mask])
        return []

    outcomes: dict[str, str] = {}
    conflicts: list[dict] = []
    used: set = set()
    drop: set = set()

    def _conf(pair: dict) -> float:
        try:
            return float(pair.get("confidence") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    # En güvenilir çift önce: aynı satıra bağlı iki kabul varsa sonuç
    # karar sırasına göre değişmesin.
    for pair in sorted(accept_pairs, key=lambda p: (-_conf(p), str(p.get("pair_id", "")))):
        pid = str(pair.get("pair_id", ""))
        if pid in already:
            outcomes[pid] = "unresolved:already_accepted"
            continue

        w_hits = [i for i in _locate(pair.get("wos_ut"), pair.get("wos_doi"),
                                     pair.get("wos_title"), pair.get("wos_year"), False)]
        s_hits = [i for i in _locate(pair.get("scp_ut"), pair.get("scp_doi"),
                                     pair.get("scp_title"), pair.get("scp_year"), True)]
        if len(w_hits) > 1 or len(s_hits) > 1:
            outcomes[pid] = "unresolved:ambiguous"
            continue
        w_idx = w_hits[0] if w_hits else None
        s_idx = s_hits[0] if s_hits else None

        if w_idx is None and s_idx is None:
            outcomes[pid] = "unresolved:both_missing"
            continue
        if w_idx is None:
            # Scopus satırı duruyor ama WoS tarafı yok (kullanıcı silmiş
            # olabilir) — tek başına kalan satır SİLİNMEZ, veri kaybolmasın.
            outcomes[pid] = "unresolved:wos_missing"
            continue
        if s_idx is None:
            # Scopus satırı zaten yok: karar fiilen uygulanmış durumda.
            outcomes[pid] = "noop_scp_missing"
            continue
        if w_idx in used or s_idx in used or w_idx in drop or s_idx in drop:
            outcomes[pid] = "unresolved:row_already_merged"
            continue

        w_row = df.loc[w_idx].to_dict()
        s_row = df.loc[s_idx].to_dict()
        merged, pair_conflicts = merge_pair_with_preferences(pid, w_row, s_row, cols)
        if "DB_Original" in merged and "DB_Original" not in df.columns:
            df["DB_Original"] = ""
        for col, val in merged.items():
            if col not in df.columns or col == "UID":
                continue
            if val == "" and df[col].dtype != object:
                continue  # sayısal kolona boş dize yazma
            df.at[w_idx, col] = val
        # Birleştirilmiş CR'ın referans sayısı değiştiyse NR'ı düzelt.
        if "CR" in merged and "NR" in df.columns:
            new_cr = _to_str(merged.get("CR"))
            if new_cr and new_cr != _to_str(w_row.get("CR")):
                df["NR"] = df["NR"].astype(object)
                df.at[w_idx, "NR"] = count_refs(new_cr)

        conflicts.extend(c for c in pair_conflicts if c.get("field") not in ("UID", "NR"))
        used.add(w_idx)
        drop.add(s_idx)
        outcomes[pid] = "merged"

    if drop:
        df = df.drop(index=list(drop)).reset_index(drop=True)
    return df, outcomes, conflicts


def decide_borderline(project_id: str, decisions: list[dict]) -> dict[str, Any]:
    """Kullanıcının borderline kararlarını uygula (aktif analiz üzerinde).

    decisions: [{pair_id, decision: 'accept'|'reject'|'skip'}]
    """
    if not decisions:
        return {"applied": 0, "snapshot": None, "pending_after": 0}

    adir = analyses.get_active_analysis_dir(project_id)
    if adir is None:
        raise RuntimeError("Aktif analiz yok — önce Smart Merge çalıştırın")

    # Dataset yolunu resolver'dan al — uzantı (parquet/legacy xlsx) burada
    # varsayılmaz; legacy proje ilk dokunuşta parquet'e taşınmış olabilir.
    merged_path = analyses.active_dataset_path(project_id)
    if merged_path is None or not merged_path.exists():
        raise RuntimeError("Birleştirilmiş dataset bulunamadı — önce Smart Merge çalıştırın")

    bq_path = adir / "borderline_queue.xlsx"
    if not bq_path.exists():
        return {"applied": 0, "snapshot": None, "pending_after": 0}

    # State güncelle
    state = _read_borderline_state(project_id, adir=adir)
    # Bu çağrıdan ÖNCE zaten kabul edilmiş çiftler — tekrar kabul edilirse
    # ikinci kez birleştirilmez (idempotent).
    prior_accepted = {pid for pid, v in state.items() if v.get("status") == "accept"}
    bq_df = pd.read_excel(bq_path)
    bq_by_id = {str(r["pair_id"]): r for _, r in bq_df.iterrows()}

    accept_pairs: list[dict] = []
    now_ts = time.time()
    for d in decisions:
        pair_id = d.get("pair_id")
        decision = d.get("decision")
        if not pair_id or decision not in ("accept", "reject", "skip"):
            continue
        # DOI belirleyici: arındırılmış DOI'leri farklı olan çift ASLA aynı yayın
        # değildir. Eski (düzeltme öncesi) kuyruktan gelmiş olsa bile hiçbir karar
        # uygulanmaz — kural display katmanında (list_borderline) olduğu gibi apply
        # katmanında da yetkilidir; doğrudan API çağrısıyla yanlış birleştirme olmaz.
        bq_row = bq_by_id.get(pair_id)
        if bq_row is not None and doi_conflict(bq_row.get("wos_doi"), bq_row.get("scp_doi")):
            continue
        state[pair_id] = {
            **state.get(pair_id, {}),
            "status": decision,
            "decided_at": now_ts,
        }
        if decision == "accept" and pair_id in bq_by_id:
            # "accept" state'e HEMEN yazılmaz; dataset'te karşılığı bulunup
            # gerçekten uygulandıktan sonra yazılır (aşağıda).
            state.pop(pair_id, None)
            state[pair_id] = {**state.get(pair_id, {}), "status": "pending"}
            accept_pairs.append(bq_by_id[pair_id])

    _write_borderline_state(project_id, state, adir=adir)

    # Kabul edilenler için dataset güncellemesi — GERÇEK BİRLEŞTİRME.
    snapshot_rel: Optional[str] = None
    applied = 0
    merged_pairs = 0
    conflicts_logged = 0
    resolved_ids: list[str] = []
    unresolved_ids: list[str] = []
    unresolved_reasons: dict[str, str] = {}
    if accept_pairs:
        df_before = dataset_io.read_dataset(merged_path)
        df_after, outcomes, pair_conflicts = _merge_accepted_pairs(
            df_before.copy(), accept_pairs, prior_accepted,
        )
        for pid, outcome in outcomes.items():
            if outcome in ("merged", "noop_scp_missing", "unresolved:already_accepted"):
                resolved_ids.append(pid)
            else:
                unresolved_ids.append(pid)
                unresolved_reasons[pid] = outcome.split(":", 1)[-1]
        merged_pairs = sum(1 for o in outcomes.values() if o == "merged")

        if merged_pairs:
            # Değişmezler — ihlalde dataset'e DOKUNULMAZ.
            if len(df_after) != len(df_before) - merged_pairs:
                raise RuntimeError(
                    f"borderline birleştirme değişmezi: {len(df_before)} - {merged_pairs} "
                    f"beklenirken {len(df_after)} satır"
                )
            if "UID" in df_after.columns and not df_after["UID"].astype(str).is_unique:
                raise RuntimeError("borderline birleştirme sonrası UID tekil değil")

            # Snapshot ÖNCEKİ dataset'ten — yalnız gerçekten bir şey değişecekse.
            snaps_dir = analyses.work_dir(project_id) / "snapshots"
            snaps_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:6]
            snap_path = snaps_dir / f"pre_borderline_accept_{stamp}.parquet"
            dataset_io.atomic_write_dataset(df_before, snap_path)
            snapshot_rel = str(snap_path.relative_to(storage.settings.storage_path))

            dataset_io.atomic_write_dataset(df_after, merged_path)
            filter_engine._DF_CACHE.clear()
            applied = merged_pairs

            # Yan artefaktlar — en iyi çaba; hata kararı geri almaz.
            try:
                conflicts_logged = _append_conflict_log(adir / "conflict_log.xlsx", pair_conflicts)
            except Exception:
                conflicts_logged = 0
            try:
                _refresh_statistic_after_borderline(adir, df_after)
            except Exception:
                pass

    # Uygulanan kabuller şimdi state'e yazılır; uygulanamayanlar "pending"
    # kalır (kuyrukta görünmeye devam eder).
    if accept_pairs:
        for pid in resolved_ids:
            state[pid] = {**state.get(pid, {}), "status": "accept", "decided_at": now_ts}
        for pid in unresolved_ids:
            state[pid] = {**state.get(pid, {}), "status": "pending", "unresolved_at": now_ts,
                          "unresolved_reason": unresolved_reasons.get(pid, "")}
    _write_borderline_state(project_id, state, adir=adir)

    # Audit
    _bl_accept = len([d for d in decisions if d.get("decision") == "accept"])
    _bl_reject = len([d for d in decisions if d.get("decision") == "reject"])
    _bl_skip = len([d for d in decisions if d.get("decision") == "skip"])
    audit.write(
        project_id,
        kind="merge_borderline",
        title=f"Borderline: {_bl_accept} kabul / {_bl_reject} red / {_bl_skip} atlandı",
        title_key="audit.titles.borderlineDecisions",
        title_params={"accept": _bl_accept, "reject": _bl_reject, "skip": _bl_skip},
        details={
            "decisions_count": len(decisions),
            "applied_changes": applied,
            "accept_pair_ids": [d.get("pair_id") for d in decisions if d.get("decision") == "accept"][:50],
            "unresolved_pair_ids": unresolved_ids[:50],
            "unresolved_reasons": {k: unresolved_reasons[k] for k in unresolved_ids[:50] if k in unresolved_reasons},
            "merged_pairs": merged_pairs,
            "conflicts_logged": conflicts_logged,
        },
        snapshot=snapshot_rel,
        user_action="borderline_decide",
    )

    pending_after = sum(1 for v in state.values() if v.get("status") == "pending")
    return {
        "applied": applied,
        "snapshot": snapshot_rel,
        "pending_after": pending_after,
        # Kabul edilip dataset'te karşılığı bulunamayan çiftler — arayüz
        # bunları "uygulandı" diye göstermemeli.
        "unresolved": unresolved_ids,
        "unresolved_reasons": unresolved_reasons,
        "merged_pairs": merged_pairs,
    }
