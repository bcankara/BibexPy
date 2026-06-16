"""Smart Merge pipeline for deduplicating and combining bibliographic sources.

Runs a self-contained six-phase pipeline independent of the core merge path:
  1. Normalize  : DOI, title, year, surname, ISSN/PMID/UT
  2. Block      : group candidates by (year, surname initial)
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

import pandas as pd

from config import settings
from jobs.runner import JobContext
from services import analyses, audit, filter_engine, storage
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


def normalize_doi(raw: Any) -> Optional[str]:
    """Bir DOI'yi kanonik biçime indir.

    Örnekler:
      'https://doi.org/10.1234/ABC'  → '10.1234/abc'
      'http://dx.doi.org/10.x'        → '10.x'
      '10.1234/abc/'                  → '10.1234/abc'
    """
    s = _to_str(raw)
    if not s:
        return None
    s = s.lower()
    s = _DOI_PREFIX_RE.sub("", s)
    s = s.rstrip("/. \t")
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


# ════════════════════════════════════════════════════════════════════════
#  FAZ 3 — Multi-stage matching
# ════════════════════════════════════════════════════════════════════════

def negative_rule_check(w: dict, s: dict) -> Optional[str]:
    """Belirleyici negatif kurallar: çelişen kimlikler → reject (asla aynı yayın).

    DOI BELİRLEYİCİDİR. İki kaydın da normalize edilmiş DOI'si varsa ve FARKLIYSA,
    bunlar kesinlikle farklı yayınlardır — DOI kalıcı, yayına-özgü tanımlayıcıdır.
    Başlık/journal benzerliği ne olursa olsun eşleşmezler ve borderline (manuel onay)
    kuyruğuna ASLA girmezler. Aynı çelişki mantığı PMID ve ISSN için de geçerlidir
    (Hammerton 2013).

    Both sides have value AND they're different → reject. Hiçbir tarafta yoksa geç
    (ör. yalnız bir tarafta DOI varsa, başlık eşleştirmesine düşülür).

    NOT: UT/EID cross-database aynı değildir (WoS UT 'WOS:xxx', Scopus EID '2-s2.0-xxx').
    Bu yüzden UT negative rule olarak KULLANILMAZ. Sadece aynı kaynak içi olur.
    PMID ise cross-database aynıdır (PubMed Identifier).
    """
    for key in ("_norm_doi", "_norm_pmid", "_norm_issn"):
        wv = w.get(key)
        sv = s.get(key)
        if wv and sv and wv != sv:
            return f"{key.replace('_norm_', '').upper()} mismatch ({wv} ≠ {sv})"
    return None


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
    return bool(a and b and a != b)


def compute_match(w: dict, s: dict) -> Optional[dict]:
    """5-aşamalı eşleştirme kararı.

    Dönüş: None (no match) veya {stage, confidence, reason, jw_title, year_diff, surname_match}
    """
    # Stage 0 — Negative rules (reject)
    neg = negative_rule_check(w, s)
    if neg:
        return None

    # Stage 1 — DOI exact
    w_doi = w.get("_norm_doi")
    s_doi = s.get("_norm_doi")
    if w_doi and s_doi and w_doi == s_doi:
        return {
            "stage": "1_doi_exact",
            "stage_label": "DOI exact",
            "confidence": 1.00,
            "reason": f"DOI exact: {w_doi}",
            "jw_title": None,
            "year_diff": None,
            "surname_match": None,
        }

    # Stage 2 — PMID exact (UT cross-database aynı değildir, sadece PMID)
    w_pmid = w.get("_norm_pmid")
    s_pmid = s.get("_norm_pmid")
    if w_pmid and s_pmid and w_pmid == s_pmid:
        return {
            "stage": "2_pmid_exact",
            "stage_label": "PMID exact",
            "confidence": 0.99,
            "reason": f"PMID exact: {w_pmid}",
            "jw_title": None,
            "year_diff": None,
            "surname_match": None,
        }

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

        if (
            jw_title >= TITLE_EXACT_THRESHOLD
            and year_diff is not None
            and year_diff <= YEAR_TOLERANCE
            and surname_match
        ):
            return {
                "stage": "3_title_year_surname",
                "stage_label": "Title+Year+Surname",
                "confidence": 0.95,
                "reason": f"JW(title)={jw_title:.3f} ≥ {TITLE_EXACT_THRESHOLD}, year_diff={year_diff}, surname='{w_surname}' eşleşti",
                "jw_title": round(jw_title, 4),
                "year_diff": year_diff,
                "surname_match": surname_match,
            }

        # Stage 4 — Journal + Volume + (Pages veya BP) — DOI'siz eski yayınlar için
        w_journal = w.get("_norm_journal", "")
        s_journal = s.get("_norm_journal", "")
        if w_journal and s_journal:
            jw_journal = jaro_winkler(w_journal, s_journal)
            w_vol = _to_str(w.get("VL", ""))
            s_vol = _to_str(s.get("VL", ""))
            w_bp = _to_str(w.get("BP", ""))
            s_bp = _to_str(s.get("BP", ""))
            w_pg = _to_str(w.get("PG", ""))
            s_pg = _to_str(s.get("PG", ""))
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
                    "reason": f"JW(journal)={jw_journal:.3f}, vol={w_vol}, page_match=True",
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
                "reason": f"JW(title)={jw_title:.3f}, year_diff={year_diff}, surname_match={surname_match}",
                "jw_title": round(jw_title, 4),
                "year_diff": year_diff,
                "surname_match": surname_match,
            }

    return None


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

    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        general.to_excel(writer, sheet_name="General Stats", index=False)
        fields.to_excel(writer, sheet_name="Field Stats", index=False)


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

    # DB etiketle
    wos_df = wos_df.copy()
    scp_df = scp_df.copy()
    wos_df["DB"] = "ISI"
    scp_df["DB"] = "SCOPUS"

    # 2. Normalize — yan kolonlar
    ctx.log(f"Normalization: {len(wos_df) + len(scp_df)} rows")
    for df in (wos_df, scp_df):
        df["_norm_doi"] = df.get("DI", pd.Series([""] * len(df))).apply(normalize_doi)
        df["_norm_title"] = df.get("TI", pd.Series([""] * len(df))).apply(normalize_title)
        df["_norm_year"] = df.get("PY", pd.Series([""] * len(df))).apply(normalize_year)
        df["_norm_surname"] = df.get("AU", pd.Series([""] * len(df))).apply(normalize_author_surname)
        df["_norm_issn"] = df.get("SN", pd.Series([""] * len(df))).apply(normalize_issn)
        df["_norm_pmid"] = df.get("PM", pd.Series([""] * len(df))).apply(normalize_id_token)
        df["_norm_ut"] = df.get("UT", pd.Series([""] * len(df))).apply(normalize_id_token)
        df["_norm_journal"] = df.get("SO", pd.Series([""] * len(df))).apply(normalize_title)
    ctx.progress(0.20)

    # 3. Blocking
    wos_blocks = build_blocks(wos_df)
    scp_blocks = build_blocks(scp_df)
    common_keys = set(wos_blocks.keys()) & set(scp_blocks.keys())
    ctx.log(f"Blocking: {len(wos_blocks)} WoS blocks × {len(scp_blocks)} Scopus blocks -> {len(common_keys)} common blocks")
    ctx.progress(0.25)

    # 4. Multi-stage matching (greedy assignment)
    # Her WoS satırı en yüksek confidence Scopus satırıyla eşleşir (one-to-one)
    matches: list[dict] = []           # match_audit rows
    borderline: list[dict] = []        # borderline_queue rows
    matched_wos: set[int] = set()
    matched_scp: set[int] = set()

    # Sayaçlar
    stage_counts: dict[str, int] = {}

    # Tüm aday çiftleri topla, confidence descending sırala
    candidates: list[tuple[float, int, int, dict]] = []
    pair_counter = 0
    for key in common_keys:
        for w_idx in wos_blocks[key]:
            w_row = wos_df.loc[w_idx].to_dict()
            for s_idx in scp_blocks[key]:
                s_row = scp_df.loc[s_idx].to_dict()
                m = compute_match(w_row, s_row)
                if m is None:
                    continue
                candidates.append((m["confidence"], w_idx, s_idx, m))

    # Confidence descending
    candidates.sort(key=lambda x: -x[0])
    ctx.log(f"Candidate pairs: {len(candidates)}")
    ctx.progress(0.40)

    # Greedy assignment + borderline ayır
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
    ctx.log(f"Matches: {len(matches)} exact, {len(borderline)} borderline")
    for stage_label, n in stage_counts.items():
        ctx.log(f"  Stage [{stage_label}]: {n}")
    ctx.progress(0.65)

    # 5. Field merge with preferences
    ctx.log("Field merge (Caputo 2024 defaults)...")
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

    # Tek bir DataFrame
    final_df = pd.concat([merged_df, wos_not_matched, scp_not_matched], ignore_index=True)
    ctx.progress(0.80)

    # 7. UID kolonu ekle (filter_engine ile aynı şema)
    filter_engine._ensure_uid_column(final_df)

    # 8. Çıktıları yaz — analiz klasörüne
    ctx.log("Writing output files...")
    merged_xlsx = adir / "merged.xlsx"
    audit_xlsx = adir / "match_audit.xlsx"
    conflict_xlsx = adir / "conflict_log.xlsx"
    borderline_xlsx = adir / "borderline_queue.xlsx"
    lost_wos_xlsx = adir / "Lost_Wos_Records.xlsx"
    lost_scp_xlsx = adir / "Lost_Scopus_Records.xlsx"
    stat_xlsx = adir / "Statistic.xlsx"

    try:
        await asyncio.to_thread(final_df.to_excel, merged_xlsx, index=False)
        await asyncio.to_thread(_write_match_audit, matches, audit_xlsx)
        await asyncio.to_thread(_write_conflict_log, conflicts, conflict_xlsx)
        await asyncio.to_thread(_write_borderline_queue, borderline, borderline_xlsx)
        await asyncio.to_thread(_write_lost_records, wos_not_matched, lost_wos_xlsx)
        await asyncio.to_thread(_write_lost_records, scp_not_matched, lost_scp_xlsx)
        await asyncio.to_thread(
            _write_statistic_smart,
            len(final_df), len(wos_df), len(scp_df), final_df, stat_xlsx,
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
        "scopus_input": int(len(scp_df)),
        "wos_input": int(len(wos_df)),
        "merged_count": int(len(final_df)),
        "matched_pairs": int(len(matches)),
        "borderline_count": int(len(borderline)),
        "borderline_pending": int(len(borderline)),
        "conflict_count": int(len(conflicts)),
        "match_stages": stage_counts,
        "field_source_distribution": field_source_distribution,
        "lost_wos_count": int(len(wos_not_matched)),
        "lost_scopus_count": int(len(scp_not_matched)),
        "output_xlsx": str(merged_xlsx.relative_to(storage.settings.storage_path)),
        "output_files": [
            f.name for f in (
                merged_xlsx, audit_xlsx, conflict_xlsx, borderline_xlsx,
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


def decide_borderline(project_id: str, decisions: list[dict]) -> dict[str, Any]:
    """Kullanıcının borderline kararlarını uygula (aktif analiz üzerinde).

    decisions: [{pair_id, decision: 'accept'|'reject'|'skip'}]
    """
    if not decisions:
        return {"applied": 0, "snapshot": None, "pending_after": 0}

    adir = analyses.get_active_analysis_dir(project_id)
    if adir is None:
        raise RuntimeError("Aktif analiz yok — önce Smart Merge çalıştırın")

    merged_xlsx = adir / "merged.xlsx"
    if not merged_xlsx.exists():
        raise RuntimeError("merged.xlsx bulunamadı — önce Smart Merge çalıştırın")

    bq_path = adir / "borderline_queue.xlsx"
    if not bq_path.exists():
        return {"applied": 0, "snapshot": None, "pending_after": 0}

    # State güncelle
    state = _read_borderline_state(project_id, adir=adir)
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
            accept_pairs.append(bq_by_id[pair_id])

    _write_borderline_state(project_id, state, adir=adir)

    # Kabul edilenler için dataset güncellemesi
    snapshot_rel: Optional[str] = None
    applied = 0
    if accept_pairs:
        df = pd.read_excel(merged_xlsx)
        # Snapshot
        snaps_dir = storage.project_dir(project_id) / "snapshots"
        snaps_dir.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        snap_path = snaps_dir / f"pre_borderline_accept_{stamp}.xlsx"
        df.to_excel(snap_path, index=False)
        snapshot_rel = str(snap_path.relative_to(storage.settings.storage_path))

        # Yeniden yükle WoS/Scopus için pair satırlarını birleştir
        # Basit yaklaşım: accept edilen Scopus index'ini sil (WoS satırı zaten merged_df'te)
        # Aslında merged_smart.xlsx'te ne WoS-only ne Scopus-only kayıtların ikisi de var
        # (eşleşmemiş olarak). Şimdi accept = "bunlar aynı yayın" → Scopus satırını sil.
        # Pair'lerden scp_doi'yi al, df'te o DOI'li satırı sil.
        to_drop_dois: set[str] = set()
        for pair in accept_pairs:
            scp_doi = str(pair.get("scp_doi", "")).strip().lower()
            if scp_doi:
                to_drop_dois.add(scp_doi)
        if to_drop_dois and "DI" in df.columns:
            mask = df["DI"].astype(str).str.strip().str.lower().isin(to_drop_dois)
            applied = int(mask.sum())
            df = df.loc[~mask].reset_index(drop=True)
            df.to_excel(merged_xlsx, index=False)
            filter_engine._DF_CACHE.clear()

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
        },
        snapshot=snapshot_rel,
        user_action="borderline_decide",
    )

    pending_after = sum(1 for v in state.values() if v.get("status") == "pending")
    return {
        "applied": applied,
        "snapshot": snapshot_rel,
        "pending_after": pending_after,
    }
