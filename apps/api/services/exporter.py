"""Export merged bibliographic data, optionally filtered, to various file formats.

Supports WoS plain-text, VOSviewer tab-text, BibTeX, RIS, CSV, XLSX, and TSV
output. The exported records are loaded from a project's merged dataset and an
optional filter spec is applied before writing.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from fastapi import HTTPException

from services import filter_engine, storage
from services.bibex_adapter import _suppress_stdio


VALID_FORMATS = {"wos", "vos", "bib", "ris", "csv", "xlsx", "tsv"}

# Structured-table formats that biblioshiny/bibliometrix can import directly.
# Their loader assumes an SR column already exists (wcTable/countryTable do
# rep(M$SR, ...) with no guard), so these exports must carry SR.
_SR_FORMATS = {"xlsx", "csv", "tsv"}

# Format anahtarı → gerçek dosya uzantısı (anahtardan farklı olanlar). WoS plain-text
# ve VOSviewer tab-text çıktıları .txt'dir (.wos/.vos standart değil; WoS = savedrecs.txt).
_EXT = {"wos": "txt", "vos": "txt"}


def _project_paths(project_id: str) -> tuple[Path, Path]:
    meta = storage.get_project(project_id)
    if meta is None:
        raise HTTPException(404, "Proje bulunamadı")
    root = storage.project_dir(project_id)
    exports = root / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    return root, exports


def _load_filtered(project_id: str, spec: Optional[dict[str, Any]]) -> pd.DataFrame:
    df = filter_engine.load_merged(project_id)
    if spec:
        df = filter_engine.apply_filter(df, spec)
    return df


# ── SR (Short Reference) — bibliometrix/biblioshiny uyumu ────────────────

def _blank(v: Any) -> bool:
    s = str(v).strip()
    return s == "" or s.upper() in ("NAN", "NONE", "NA")


def _fmt_year(v: Any) -> str:
    """PY hücresini R'ın paste()'inin yazacağı gibi yaz: 2020.0 → "2020"."""
    if _blank(v):
        return "NA"
    try:
        f = float(v)
        if f.is_integer():
            return str(int(f))
    except (TypeError, ValueError):
        pass
    return str(v).strip()


def _first_author(au: Any) -> str:
    """AU'nun ilk ';' parçası, virgüller boşluğa çevrilmiş (bibliometrix SR())."""
    if _blank(au):
        return "NA"
    first = str(au).split(";")[0].strip().replace(",", " ")
    first = re.sub(r"\s+", " ", first).strip()
    return first or "NA"


def _sr_source(row: pd.Series, has_j9: bool, has_ji: bool, has_so: bool) -> str:
    """Kaynak kısaltması: J9 → (J9+JI boşsa SO) → (kalan boş J9 için JI, noktalar
    boşluğa). J9 kolonu hiç yoksa JI (boşsa SO) kullanılır — bibliometrix SR()
    ile aynı öncelik zinciri."""
    j9 = row.get("J9") if has_j9 else None
    ji = row.get("JI") if has_ji else None
    so = row.get("SO") if has_so else None
    if has_j9:
        if not _blank(j9):
            return str(j9).strip()
        if _blank(ji):
            return "" if _blank(so) else str(so).strip()
        return re.sub(r"\s+", " ", str(ji).replace(".", " ")).strip()
    val = ji if not _blank(ji) else so
    if _blank(val):
        return ""
    return re.sub(r"\s+", " ", str(val).replace(".", " ")).strip()


def ensure_sr(df: pd.DataFrame) -> pd.DataFrame:
    """SR / SR_FULL kolonlarını yoksa üret (bibliometrix `metaTagExtraction`
    Field="SR" algoritmasına sadık).

    biblioshiny, xlsx/csv import'unda convert2df ÇAĞIRMAZ: dosyayı ham okur ve
    SR'nin zaten var olduğunu varsayar (`wcTable` içinde korumasız
    `rep(M$SR, lengths(WC))` — SR yoksa "differing number of rows: 0, N" ile
    yükleme çöker). Format: "SOYAD AD, YIL, KAYNAK-KISALTMASI"; duplikeler
    bibliometrix'in İTERATİF kuralıyla ayrıştırılır (3 kopya → X, X-a, X-a-b).
    """
    if "SR" in df.columns and df["SR"].astype(str).str.strip().ne("").any():
        return df
    if "AU" not in df.columns:
        return df  # SR üretilemez; biblioshiny'ye ham veri de zaten yetmezdi

    has_j9 = "J9" in df.columns
    has_ji = "JI" in df.columns
    has_so = "SO" in df.columns

    parts = []
    for _, row in df.iterrows():
        fa = _first_author(row.get("AU"))
        py = _fmt_year(row.get("PY")) if "PY" in df.columns else "NA"
        src = _sr_source(row, has_j9, has_ji, has_so)
        sr = f"{fa}, {py}, {src}" if src else f"{fa}, {py}"
        parts.append(re.sub(r"\s+", " ", sr).strip())

    sr_full = list(parts)
    # Bibliometrix'in birleşik (compounding) süffiks döngüsü: her turda
    # duplicated() sonrası kalanlara -a, sonra -b ... eklenir.
    letters = "abcdefghijklmnopqrstuvwxyz"
    sr = list(parts)
    for i in range(len(letters)):
        seen: set = set()
        dup_idx = []
        for idx, v in enumerate(sr):
            if v in seen:
                dup_idx.append(idx)
            else:
                seen.add(v)
        if not dup_idx:
            break
        for idx in dup_idx:
            sr[idx] = f"{sr[idx]}-{letters[i]}"

    out = df.copy(deep=False)
    out["SR"] = sr
    if "SR_FULL" not in out.columns:
        out["SR_FULL"] = sr_full
    return out


def export(
    project_id: str,
    fmt: str,
    filter_spec: Optional[dict[str, Any]] = None,
    output_name: Optional[str] = None,
) -> Path:
    if fmt not in VALID_FORMATS:
        raise HTTPException(400, f"Desteklenmeyen format: {fmt}")
    _, exports = _project_paths(project_id)

    try:
        df = _load_filtered(project_id, filter_spec)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))

    if len(df) == 0:
        raise HTTPException(400, "Filtre 0 kayıt döndürdü — export yapılmadı")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    ext = _EXT.get(fmt, fmt)
    if output_name:
        name = output_name
        if not name.lower().endswith(f".{ext}"):
            name = f"{name}.{ext}"
    else:
        # wos/vos gibi anahtar≠uzantı durumunda formatı isimde tut: export_..._wos.txt
        name = f"export_{stamp}_{fmt}.{ext}" if ext != fmt else f"export_{stamp}.{ext}"
    output = exports / Path(name).name

    if fmt in _SR_FORMATS:
        df = ensure_sr(df)

    if fmt == "xlsx":
        df.to_excel(output, index=False)
    elif fmt == "csv":
        df.to_csv(output, index=False, encoding="utf-8")
    elif fmt == "tsv":
        df.to_csv(output, sep="\t", index=False, encoding="utf-8")
    elif fmt == "wos":
        # Geçici XLSX üzerinden bibex_core.xlsx2vos
        from bibex_core.xlsx2vos import convert_excel_to_wos
        tmp_xlsx = exports / f"_tmp_{stamp}.xlsx"
        df.to_excel(tmp_xlsx, index=False)
        with _suppress_stdio():
            convert_excel_to_wos(str(tmp_xlsx), str(output))
        tmp_xlsx.unlink(missing_ok=True)
    elif fmt == "vos":
        # VOSviewer için tab-separated (bibliometrix uyumlu temel kolonlar)
        cols = [c for c in ("AU", "TI", "SO", "PY", "VL", "IS", "PG", "DI", "DE", "ID", "AB", "TC", "DT", "DB", "WC", "SC")
                if c in df.columns]
        df[cols].to_csv(output, sep="\t", index=False, encoding="utf-8")
    elif fmt == "bib":
        from services.bibtex_writer import write_bibtex
        write_bibtex(df, output)
    elif fmt == "ris":
        from services.ris_writer import write_ris
        write_ris(df, output)
    else:
        raise HTTPException(500, "İç hata")

    storage.touch_project(project_id)
    return output


def list_exports(project_id: str) -> list[dict]:
    _, exports = _project_paths(project_id)
    out = []
    for f in sorted(exports.iterdir(), reverse=True):
        if not f.is_file():
            continue
        out.append({
            "name": f.name,
            "size": f.stat().st_size,
            "relative_path": str(f.relative_to(storage.settings.storage_path)),
        })
    return out
