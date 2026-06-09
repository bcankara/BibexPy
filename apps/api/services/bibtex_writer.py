"""DataFrame -> BibTeX (.bib) yazıcı.

BibexPy alan adlarını BibTeX'e eşler:
    AU -> author (semicolon -> ' and ')
    TI -> title
    SO -> journal
    PY -> year
    VL -> volume, IS -> number, AR -> articleno
    DI -> doi, URL -> url, AB -> abstract
    DE -> keywords (semicolon -> ', ')
    PU -> publisher
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd


def _escape(value: str) -> str:
    if not value:
        return ""
    s = str(value)
    # Yaygın LaTeX kaçırma — minimum (literal tutmak için)
    s = s.replace("\\", "\\textbackslash{}")
    for ch in "&%$#_{}":
        s = s.replace(ch, f"\\{ch}")
    s = s.replace("~", "\\textasciitilde{}")
    s = s.replace("^", "\\textasciicircum{}")
    return s


def _normalize_authors(au: str) -> str:
    parts = [a.strip() for a in re.split(r";|\band\b", str(au)) if a.strip()]
    return " and ".join(parts)


def _normalize_keywords(de: str) -> str:
    parts = [k.strip() for k in str(de).split(";") if k.strip()]
    return ", ".join(parts)


def _slug(text: str, maxlen: int = 30) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "", str(text))
    return s[:maxlen] or "anon"


def _entry_type(dt: str) -> str:
    dt = str(dt).upper()
    if "REVIEW" in dt: return "article"
    if "CONFERENCE" in dt or "PROCEEDING" in dt: return "inproceedings"
    if "BOOK" in dt: return "book"
    if "CHAPTER" in dt: return "incollection"
    return "article"


def write_bibtex(df: pd.DataFrame, output: Path) -> Path:
    used_keys: set[str] = set()
    lines: list[str] = []
    for i, row in df.iterrows():
        au = row.get("AU", "") or ""
        py = str(row.get("PY", "") or "").strip()
        ti = row.get("TI", "") or ""
        # cite key: SmithEtAl2020Title
        first_author = re.split(r";|,|\band\b", str(au))[0].strip()
        first_author = _slug(first_author.split()[0] if first_author else "Anon", 20)
        key = f"{first_author}{py}{_slug(ti, 12)}"
        # Uniqueness
        base, n = key, 1
        while key in used_keys:
            key = f"{base}_{n}"; n += 1
        used_keys.add(key)

        etype = _entry_type(row.get("DT", "") or "")
        lines.append(f"@{etype}{{{key},")
        fields = {
            "author": _normalize_authors(au),
            "title": str(ti),
            "journal": str(row.get("SO", "") or ""),
            "year": py,
            "volume": str(row.get("VL", "") or ""),
            "number": str(row.get("IS", "") or ""),
            "pages": str(row.get("PG", "") or ""),
            "doi": str(row.get("DI", "") or ""),
            "url": str(row.get("URL", "") or ""),
            "abstract": str(row.get("AB", "") or ""),
            "keywords": _normalize_keywords(row.get("DE", "") or ""),
            "publisher": str(row.get("PU", "") or ""),
        }
        for k, v in fields.items():
            v = v.strip()
            if not v or v.lower() == "nan":
                continue
            lines.append(f"  {k} = {{{_escape(v)}}},")
        # son virgülü temizle
        if lines[-1].endswith(","):
            lines[-1] = lines[-1][:-1]
        lines.append("}")
        lines.append("")

    output.write_text("\n".join(lines), encoding="utf-8")
    return output
