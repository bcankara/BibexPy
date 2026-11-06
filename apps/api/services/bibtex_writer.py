"""Write a DataFrame of bibliographic records to a BibTeX (.bib) file.

Maps BibexPy field names to BibTeX fields (e.g. AU -> author, TI -> title,
SO -> journal, PY -> year, DI -> doi, DE -> keywords), normalizes author and
keyword lists, escapes LaTeX special characters, and generates unique entry keys.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from services import pdcompat


def _cell(row, key: str) -> str:
    """A cell as text; missing values (None, NaN, legacy "nan") become ""."""
    v = row.get(key, "")
    if pdcompat.is_missing(v):
        return ""
    t = str(v)
    return "" if t.strip().lower() == "nan" else t


def _escape(value: str) -> str:
    if not value:
        return ""
    s = str(value)
    # Common LaTeX escaping — minimal (to keep things literal)
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
        au = _cell(row, "AU")
        py = _cell(row, "PY").strip()
        ti = _cell(row, "TI")
        # cite key: SmithEtAl2020Title
        first_author = re.split(r";|,|\band\b", str(au))[0].strip()
        first_author = _slug(first_author.split()[0] if first_author else "Anon", 20)
        key = f"{first_author}{py}{_slug(ti, 12)}"
        # Uniqueness
        base, n = key, 1
        while key in used_keys:
            key = f"{base}_{n}"; n += 1
        used_keys.add(key)

        etype = _entry_type(_cell(row, "DT"))
        lines.append(f"@{etype}{{{key},")
        fields = {
            "author": _normalize_authors(au),
            "title": str(ti),
            "journal": _cell(row, "SO"),
            "year": py,
            "volume": _cell(row, "VL"),
            "number": _cell(row, "IS"),
            "pages": _cell(row, "PG"),
            "doi": _cell(row, "DI"),
            "url": _cell(row, "URL"),
            "abstract": _cell(row, "AB"),
            "keywords": _normalize_keywords(_cell(row, "DE")),
            "publisher": _cell(row, "PU"),
        }
        for k, v in fields.items():
            v = v.strip()
            if not v or v.lower() == "nan":
                continue
            lines.append(f"  {k} = {{{_escape(v)}}},")
        # strip the trailing comma
        if lines[-1].endswith(","):
            lines[-1] = lines[-1][:-1]
        lines.append("}")
        lines.append("")

    output.write_text("\n".join(lines), encoding="utf-8")
    return output
