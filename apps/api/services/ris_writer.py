"""Write a bibliographic DataFrame to a RIS reference file.

Maps record fields to RIS tags, splitting multi-valued author and keyword
fields, and serializes each row as a RIS entry via ``write_ris``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from services import pdcompat


TYPE_MAP = {
    "ARTICLE": "JOUR",
    "REVIEW": "JOUR",
    "CONFERENCE PAPER": "CPAPER",
    "BOOK": "BOOK",
    "BOOK CHAPTER": "CHAP",
    "EDITORIAL": "JOUR",
}


def _cell(row, key: str) -> str:
    """A cell as stripped text; missing values (None, NaN, legacy "nan") become ""."""
    v = row.get(key, "")
    if pdcompat.is_missing(v):
        return ""
    t = str(v).strip()
    return "" if t.lower() == "nan" else t


def _ty(dt: str) -> str:
    return TYPE_MAP.get(str(dt).upper().strip(), "JOUR")


def _split(value: str) -> list[str]:
    return [v.strip() for v in re.split(r";", str(value)) if v.strip()]


def write_ris(df: pd.DataFrame, output: Path) -> Path:
    with output.open("w", encoding="utf-8") as f:
        for _, row in df.iterrows():
            f.write(f"TY  - {_ty(_cell(row, 'DT'))}\n")
            for au in _split(_cell(row, "AU")):
                f.write(f"AU  - {au}\n")
            ti = _cell(row, "TI")
            if ti: f.write(f"TI  - {ti}\n")
            so = _cell(row, "SO")
            if so: f.write(f"JO  - {so}\n")
            ji = _cell(row, "JI")
            if ji: f.write(f"J2  - {ji}\n")
            py = _cell(row, "PY")
            if py: f.write(f"PY  - {py}\n")
            vl = _cell(row, "VL")
            if vl: f.write(f"VL  - {vl}\n")
            issue = _cell(row, "IS")
            if issue: f.write(f"IS  - {issue}\n")
            pg = _cell(row, "PG")
            if pg: f.write(f"SP  - {pg}\n")
            di = _cell(row, "DI")
            if di: f.write(f"DO  - {di}\n")
            url = _cell(row, "URL")
            if url: f.write(f"UR  - {url}\n")
            ab = _cell(row, "AB")
            if ab: f.write(f"AB  - {ab}\n")
            for kw in _split(_cell(row, "DE")):
                f.write(f"KW  - {kw}\n")
            pu = _cell(row, "PU")
            if pu: f.write(f"PB  - {pu}\n")
            sn = _cell(row, "SN")
            if sn: f.write(f"SN  - {sn}\n")
            f.write("ER  - \n\n")
    return output
