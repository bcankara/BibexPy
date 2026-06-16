"""Export merged bibliographic data, optionally filtered, to various file formats.

Supports WoS plain-text, VOSviewer tab-text, BibTeX, RIS, CSV, XLSX, and TSV
output. The exported records are loaded from a project's merged dataset and an
optional filter spec is applied before writing.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from fastapi import HTTPException

from services import filter_engine, storage
from services.bibex_adapter import _suppress_stdio


VALID_FORMATS = {"wos", "vos", "bib", "ris", "csv", "xlsx", "tsv"}

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
