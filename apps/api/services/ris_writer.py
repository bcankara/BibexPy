"""Write a bibliographic DataFrame to a RIS reference file.

Maps record fields to RIS tags, splitting multi-valued author and keyword
fields, and serializes each row as a RIS entry via ``write_ris``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd


TYPE_MAP = {
    "ARTICLE": "JOUR",
    "REVIEW": "JOUR",
    "CONFERENCE PAPER": "CPAPER",
    "BOOK": "BOOK",
    "BOOK CHAPTER": "CHAP",
    "EDITORIAL": "JOUR",
}


def _ty(dt: str) -> str:
    return TYPE_MAP.get(str(dt).upper().strip(), "JOUR")


def _split(value: str) -> list[str]:
    return [v.strip() for v in re.split(r";", str(value)) if v.strip()]


def write_ris(df: pd.DataFrame, output: Path) -> Path:
    with output.open("w", encoding="utf-8") as f:
        for _, row in df.iterrows():
            f.write(f"TY  - {_ty(row.get('DT', '') or '')}\n")
            for au in _split(row.get("AU", "")):
                f.write(f"AU  - {au}\n")
            ti = str(row.get("TI", "") or "").strip()
            if ti: f.write(f"TI  - {ti}\n")
            so = str(row.get("SO", "") or "").strip()
            if so: f.write(f"JO  - {so}\n")
            ji = str(row.get("JI", "") or "").strip()
            if ji: f.write(f"J2  - {ji}\n")
            py = str(row.get("PY", "") or "").strip()
            if py: f.write(f"PY  - {py}\n")
            vl = str(row.get("VL", "") or "").strip()
            if vl: f.write(f"VL  - {vl}\n")
            issue = str(row.get("IS", "") or "").strip()
            if issue: f.write(f"IS  - {issue}\n")
            pg = str(row.get("PG", "") or "").strip()
            if pg: f.write(f"SP  - {pg}\n")
            di = str(row.get("DI", "") or "").strip()
            if di: f.write(f"DO  - {di}\n")
            url = str(row.get("URL", "") or "").strip()
            if url: f.write(f"UR  - {url}\n")
            ab = str(row.get("AB", "") or "").strip()
            if ab: f.write(f"AB  - {ab}\n")
            for kw in _split(row.get("DE", "")):
                f.write(f"KW  - {kw}\n")
            pu = str(row.get("PU", "") or "").strip()
            if pu: f.write(f"PB  - {pu}\n")
            sn = str(row.get("SN", "") or "").strip()
            if sn: f.write(f"SN  - {sn}\n")
            f.write("ER  - \n\n")
    return output
