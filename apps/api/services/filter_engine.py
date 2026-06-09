"""Pandas tabanlı filtre motoru — UI'dan gelen filter spec'i datasete uygular.

Plan dosyasındaki şemayı izler:
    year, doc_type, language, db_source, citation_count, journal, authors,
    wc_categories, sc_categories, fulltext, quality
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

from services import merger


# ---------- Dataset yükleme (cache) ----------

_DF_CACHE: dict[tuple[str, float], pd.DataFrame] = {}


def _ensure_uid_column(df: pd.DataFrame) -> bool:
    """Her satıra benzersiz, stabil bir UID atar. UID kolonu yoksa ekler.

    Return: True = ekledi (kaydetmek gerek), False = zaten vardı.
    """
    import uuid
    if "UID" in df.columns:
        # Eksik / duplicate UID'leri doldur
        s = df["UID"].astype(str).str.strip()
        empty = (s == "") | (s.str.upper() == "NAN") | s.isna()
        dups = s.duplicated(keep="first") & ~empty
        if not empty.any() and not dups.any():
            return False
        # Eksik veya çakışan UID'lere yeni değer ata
        existing = set(s[~empty & ~dups].tolist())
        for i in df.index[empty | dups]:
            while True:
                nid = f"r_{uuid.uuid4().hex[:10]}"
                if nid not in existing:
                    existing.add(nid)
                    df.at[i, "UID"] = nid
                    break
        return True
    # UID kolonu tamamen yok
    df["UID"] = [f"r_{uuid.uuid4().hex[:10]}" for _ in range(len(df))]
    return True


def load_merged(project_id: str) -> pd.DataFrame:
    p = merger.merged_dataset_path(project_id)
    if p is None:
        raise FileNotFoundError("Birleştirilmiş veri yok — önce Merge çalıştırın")
    mtime = p.stat().st_mtime
    key = (str(p), mtime)
    if key not in _DF_CACHE:
        _DF_CACHE.clear()  # tek-kullanıcı, tek-dataset
        df = pd.read_excel(p)
        # Tüm string sütunları normalize et — boş hücreler için empty string
        for col in df.columns:
            if df[col].dtype == "object":
                df[col] = df[col].fillna("")
        # Her satıra UID ata (yoksa) ve kalıcı olarak diske yaz
        if _ensure_uid_column(df):
            try:
                df.to_excel(p, index=False)
            except Exception:
                pass  # write hatası ana akışı durdurmasın — UID memory'de yine var
        _DF_CACHE[key] = df
    return _DF_CACHE[key]


# ---------- Filter spec uygulama ----------

def _has_col(df: pd.DataFrame, col: str) -> bool:
    return col in df.columns


def _coerce_int_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _apply_range(df: pd.DataFrame, col: str, rng: dict[str, Any]) -> pd.Series:
    """Hem yıl hem citation gibi numerik aralık filtresi."""
    if not _has_col(df, col):
        return pd.Series(True, index=df.index)
    nums = _coerce_int_series(df[col])
    mask = pd.Series(True, index=df.index)
    if rng.get("min") is not None:
        mask &= nums >= float(rng["min"])
    if rng.get("max") is not None:
        mask &= nums <= float(rng["max"])
    return mask


def _apply_in(df: pd.DataFrame, col: str, values: list[str]) -> pd.Series:
    if not values or not _has_col(df, col):
        return pd.Series(True, index=df.index)
    norm = [str(v).strip().upper() for v in values if v]
    series = df[col].astype(str).str.upper().str.strip()
    return series.isin(norm)


def _apply_contains_any(df: pd.DataFrame, col: str, values: list[str]) -> pd.Series:
    """Çoklu kategori alanları (WC, SC, AU) — değerler ';' veya ',' ile ayrılmış."""
    if not values or not _has_col(df, col):
        return pd.Series(True, index=df.index)
    needles = [str(v).strip().upper() for v in values if v]
    if not needles:
        return pd.Series(True, index=df.index)
    series = df[col].astype(str).str.upper()
    pattern = "|".join(re.escape(n) for n in needles)
    return series.str.contains(pattern, na=False, regex=True)


def _fulltext_query(df: pd.DataFrame, query: str, fields: list[str]) -> pd.Series:
    """Basit AND/OR/NOT desteği. Tırnak içi ifadeler için literal eşleşme.

    Örnek: '(machine OR deep) AND learning NOT survey'
    """
    fields = [f for f in fields if _has_col(df, f)] or ["AB", "TI", "DE", "ID"]
    fields = [f for f in fields if _has_col(df, f)]
    if not fields:
        return pd.Series(True, index=df.index)

    combined = df[fields[0]].astype(str).str.upper()
    for f in fields[1:]:
        combined = combined + " || " + df[f].astype(str).str.upper()

    q = query.strip().upper()
    if not q:
        return pd.Series(True, index=df.index)

    # NOT terimleri çıkar
    not_terms: list[str] = []
    def _grab_not(m: re.Match) -> str:
        not_terms.append(m.group(1).strip().strip('"'))
        return " "
    q = re.sub(r"\bNOT\s+(\"[^\"]+\"|\S+)", _grab_not, q)

    # OR ayır (üst seviye)
    or_clauses = [c.strip() for c in re.split(r"\bOR\b", q) if c.strip()]
    if not or_clauses:
        or_clauses = [q]

    or_mask = pd.Series(False, index=df.index)
    for clause in or_clauses:
        # AND terimleri
        and_terms = [t.strip().strip('"') for t in re.split(r"\bAND\b", clause) if t.strip()]
        # Tek-terimli & parantez temizliği
        and_terms = [t.strip("() ").strip() for t in and_terms if t.strip("() ").strip()]
        if not and_terms:
            continue
        clause_mask = pd.Series(True, index=df.index)
        for term in and_terms:
            clause_mask &= combined.str.contains(re.escape(term), na=False, regex=True)
        or_mask |= clause_mask

    for nt in not_terms:
        if nt:
            or_mask &= ~combined.str.contains(re.escape(nt), na=False, regex=True)

    return or_mask


def _apply_quality(df: pd.DataFrame, quality: dict[str, Any]) -> pd.Series:
    mask = pd.Series(True, index=df.index)
    for field in quality.get("missing", []) or []:
        if not _has_col(df, field):
            continue
        s = df[field].astype(str).str.strip()
        mask &= (s == "") | (s.str.upper() == "NAN")
    for field in quality.get("has", []) or []:
        if not _has_col(df, field):
            continue
        s = df[field].astype(str).str.strip()
        mask &= (s != "") & (s.str.upper() != "NAN")
    return mask


def apply_filter(df: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    if not spec:
        return df
    mask = pd.Series(True, index=df.index)
    if "year" in spec and spec["year"]:
        mask &= _apply_range(df, "PY", spec["year"])
    if "citation_count" in spec and spec["citation_count"]:
        mask &= _apply_range(df, "TC", spec["citation_count"])
    if "doc_type" in spec:
        mask &= _apply_in(df, "DT", spec["doc_type"])
    if "language" in spec:
        mask &= _apply_in(df, "LA", spec["language"])
    if "db_source" in spec:
        mask &= _apply_in(df, "DB", spec["db_source"])
    if "journal" in spec:
        mask &= _apply_contains_any(df, "SO", spec["journal"])
    if "authors" in spec:
        mask &= _apply_contains_any(df, "AU", spec["authors"])
    if "wc_categories" in spec:
        mask &= _apply_contains_any(df, "WC", spec["wc_categories"])
    if "sc_categories" in spec:
        mask &= _apply_contains_any(df, "SC", spec["sc_categories"])
    if "fulltext" in spec and spec["fulltext"] and spec["fulltext"].get("query"):
        fields = spec["fulltext"].get("fields") or ["AB", "TI", "DE", "ID"]
        mask &= _fulltext_query(df, spec["fulltext"]["query"], fields)
    if "quality" in spec and spec["quality"]:
        mask &= _apply_quality(df, spec["quality"])
    return df.loc[mask]


# ---------- Facets ----------

DEFAULT_FACET_FIELDS = ["PY", "DT", "LA", "DB"]


def _value_counts(series: pd.Series, top: int = 30) -> list[dict]:
    s = series.astype(str).str.strip()
    s = s[(s != "") & (s.str.upper() != "NAN")]
    counts = s.value_counts().head(top)
    return [{"value": str(k), "count": int(v)} for k, v in counts.items()]


def compute_facets(df: pd.DataFrame) -> dict[str, Any]:
    facets: dict[str, Any] = {"total": int(len(df))}
    if _has_col(df, "PY"):
        years = pd.to_numeric(df["PY"], errors="coerce").dropna().astype(int)
        if len(years):
            histogram = years.value_counts().sort_index()
            facets["year"] = {
                "min": int(years.min()),
                "max": int(years.max()),
                "histogram": [{"year": int(y), "count": int(c)} for y, c in histogram.items()],
            }
    if _has_col(df, "TC"):
        cit = pd.to_numeric(df["TC"], errors="coerce").dropna().astype(int)
        if len(cit):
            facets["citation_count"] = {"min": int(cit.min()), "max": int(cit.max()), "mean": float(cit.mean())}
    for field, name in [("DT", "doc_type"), ("LA", "language"), ("DB", "db_source")]:
        if _has_col(df, field):
            facets[name] = _value_counts(df[field], top=20)
    # Çoklu-değerli alanlar için top dergi/kategori (split-by-semicolon yapmadan)
    if _has_col(df, "SO"):
        facets["journal_top"] = _value_counts(df["SO"], top=20)
    return facets


# ---------- Sayfalama + projeksiyon ----------

# Bunlar UI'da varsayılan olarak görünür kolonlar (öncelik sırası); diğerleri açılır menüden açılır.
DISPLAY_COLS = ["TI", "AU", "SO", "PY", "TC", "DT", "LA", "DI", "DE", "DB"]

# UI tarafından bilinmeyen / faydasız kolonlar (Excel index, vb.)
SKIP_COLS = {"Unnamed: 0", "ER"}


def paginate(df: pd.DataFrame, offset: int = 0, limit: int = 50, columns: Optional[list[str]] = None) -> dict:
    """Tüm kolonları döndür (kullanıcı UI'dan filtreleyebilsin). columns verilirse onları kullan.

    UID kolonu varsa her zaman dahil edilir (frontend row identification için).
    """
    if columns:
        cols = [c for c in columns if c in df.columns]
        # UID her zaman dahil — UI'da görünmese bile seçim için lazım
        if "UID" in df.columns and "UID" not in cols:
            cols = ["UID"] + cols
    else:
        # Tüm kolonlar; ama UI'da en sık görünenler önce gelsin
        all_cols = [c for c in df.columns if c not in SKIP_COLS]
        priority = [c for c in DISPLAY_COLS if c in all_cols]
        rest = [c for c in all_cols if c not in priority]
        cols = priority + sorted(rest)
        # UID en başa
        if "UID" in df.columns and "UID" in cols:
            cols = ["UID"] + [c for c in cols if c != "UID"]

    total = int(len(df))
    page = df.iloc[offset: offset + limit][cols]
    records: list[dict[str, Any]] = []
    for _, row in page.iterrows():
        rec: dict[str, Any] = {}
        for c in cols:
            v = row[c]
            if pd.isna(v):
                rec[c] = None
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                # Sayısal alanları string yap (NaN korunsun)
                rec[c] = str(int(v)) if float(v).is_integer() else str(v)
            else:
                rec[c] = str(v) if v is not None else None
        records.append(rec)
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "columns": cols,
        "records": records,
    }
