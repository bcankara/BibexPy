"""Standalone bibliometric file format converter (no project required).

Exposes the ``/tools`` router. Accepts a single uploaded file (WoS plain text,
Scopus CSV, plain CSV, TSV, or Excel) and converts it to a chosen target format
(WoS / VOSviewer TSV / BibTeX / RIS / CSV / XLSX / TSV). The result is written to
a temporary directory, streamed back in the response, then cleaned up in the
background. The endpoint is stateless and independent of any project.
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from config import settings
from services import exporter
from services.bibex_adapter import _suppress_stdio

router = APIRouter(prefix="/tools", tags=["tools"])


SourceFormat = Literal["wos", "scopus_csv", "csv", "tsv", "xlsx"]
TargetFormat = Literal["wos", "vos", "bib", "ris", "csv", "tsv", "xlsx"]

VALID_SOURCE: set[str] = {"wos", "scopus_csv", "csv", "tsv", "xlsx"}
VALID_TARGET: set[str] = {"wos", "vos", "bib", "ris", "csv", "tsv", "xlsx"}

# Hedef format anahtarı ≠ dosya uzantısı olan durumlar: WoS plain text ve VOSviewer
# TSV çıktıları aslında .txt'tir (test bulgusu #9 — eskiden .wos/.vos olarak iniyordu).
# services/exporter.py:_EXT ile AYNI standart. Listelenmeyen formatlarda anahtar=uzantı.
_EXT: dict[str, str] = {"wos": "txt", "vos": "txt"}


def _tools_tmp_dir() -> Path:
    base = settings.storage_path / "tools_tmp"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _read_source(src_path: Path, source_format: str) -> pd.DataFrame:
    """Yüklenmiş dosyayı DataFrame'e oku."""
    try:
        if source_format == "xlsx":
            return pd.read_excel(src_path)

        if source_format == "csv":
            # Plain CSV — encoding auto fallback (UTF-8 → cp1254)
            for enc in ("utf-8", "utf-8-sig", "cp1254", "latin-1"):
                try:
                    return pd.read_csv(src_path, encoding=enc)
                except UnicodeDecodeError:
                    continue
            raise HTTPException(400, "csv_encoding_failed")

        if source_format == "tsv":
            for enc in ("utf-8", "utf-8-sig", "cp1254", "latin-1"):
                try:
                    return pd.read_csv(src_path, sep="\t", encoding=enc)
                except UnicodeDecodeError:
                    continue
            raise HTTPException(400, "tsv_encoding_failed")

        if source_format == "scopus_csv":
            # bibex_core ile Scopus CSV → XLSX → read
            from bibex_core.scp2xlsx import save_to_excel
            tmp_xlsx = src_path.parent / "_scopus_tmp.xlsx"
            with _suppress_stdio():
                ok = save_to_excel([str(src_path)], str(tmp_xlsx))
            if not ok or not tmp_xlsx.exists():
                raise HTTPException(400, "scopus_convert_failed")
            df = pd.read_excel(tmp_xlsx)
            tmp_xlsx.unlink(missing_ok=True)
            return df

        if source_format == "wos":
            from bibex_core.wos2xlsx import save_to_excel
            tmp_xlsx = src_path.parent / "_wos_tmp.xlsx"
            with _suppress_stdio():
                ok = save_to_excel(str(src_path), str(tmp_xlsx))
            if not ok or not tmp_xlsx.exists():
                raise HTTPException(400, "wos_convert_failed")
            df = pd.read_excel(tmp_xlsx)
            tmp_xlsx.unlink(missing_ok=True)
            return df

        raise HTTPException(400, f"unknown_source_format: {source_format}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"file_read_failed: {e}")


def _write_target(df: pd.DataFrame, target_format: str, out_path: Path) -> None:
    """DataFrame'i hedef formatta yaz — proje Export'uyla AYNI yazıcı.

    Yazma mantığı exporter.write_dataset'te tek yerde durur; burada kopyası
    tutulduğunda iki yol birbirinden ayrışmıştı (SR üretimi yalnız Export'a
    girmiş, Tools çıktısı biblioshiny'de çökmeye devam etmişti).
    """
    if df.empty:
        raise HTTPException(400, "file_empty")
    exporter.write_dataset(df, target_format, out_path)


def _safe_cleanup(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


@router.post("/convert")
async def convert_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    source_format: str = Form(...),
    target_format: str = Form(...),
    output_name: str | None = Form(None),
):
    """Tek dosyayı yükle, hedef formata dönüştür ve indir.

    Multipart form data:
    - `file`: yüklenecek dosya
    - `source_format`: 'wos' | 'scopus_csv' | 'csv' | 'tsv' | 'xlsx'
    - `target_format`: 'wos' | 'vos' | 'bib' | 'ris' | 'csv' | 'tsv' | 'xlsx'
    - `output_name`: (opsiyonel) çıktı dosya adı — uzantısız
    """
    if source_format not in VALID_SOURCE:
        raise HTTPException(400, f"invalid_source_format: {source_format}")
    if target_format not in VALID_TARGET:
        raise HTTPException(400, f"invalid_target_format: {target_format}")

    # Geçici klasör
    job_id = uuid.uuid4().hex[:12]
    workdir = _tools_tmp_dir() / job_id
    workdir.mkdir(parents=True, exist_ok=True)

    # Yüklenen dosyayı yaz
    original_name = Path(file.filename or "input").name
    src_path = workdir / original_name
    try:
        with src_path.open("wb") as out:
            shutil.copyfileobj(file.file, out)
    except Exception as e:
        _safe_cleanup(workdir)
        raise HTTPException(500, f"file_write_failed: {e}")
    finally:
        try:
            file.file.close()
        except Exception:
            pass

    # Oku
    try:
        df = _read_source(src_path, source_format)
    except Exception:
        _safe_cleanup(workdir)
        raise

    # Yaz
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = (output_name or f"converted_{stamp}").strip() or f"converted_{stamp}"
    # Path-traversal'a karşı sadece dosya adı kısmı
    base = Path(base).name
    # Uzantıyı garanti et — wos/vos gibi anahtar≠uzantı durumunda GERÇEK uzantıyı
    # kullan (.wos/.vos değil .txt). FileResponse filename=out_path.name → otomatik düzelir.
    ext = _EXT.get(target_format, target_format)
    if not base.lower().endswith(f".{ext}"):
        base = f"{base}.{ext}"
    out_path = workdir / base

    try:
        _write_target(df, target_format, out_path)
    except Exception:
        _safe_cleanup(workdir)
        raise

    if not out_path.exists():
        _safe_cleanup(workdir)
        raise HTTPException(500, "output_file_missing")

    # Response stream sonrası temizlik
    background_tasks.add_task(_safe_cleanup, workdir)

    # Records sayısı header olarak da gönder — UI istatistik göstersin
    return FileResponse(
        path=str(out_path),
        filename=out_path.name,
        media_type="application/octet-stream",
        headers={
            "X-Records-Count": str(len(df)),
            "X-Columns-Count": str(len(df.columns)),
            "X-Source-Format": source_format,
            "X-Target-Format": target_format,
        },
    )


@router.get("/formats")
def list_formats():
    """UI için desteklenen formatlar listesi (kaynak ↔ hedef matrisi)."""
    return {
        "source_formats": [
            {"id": "wos",        "label": "WoS plain text",  "extensions": ["txt", "isi"]},
            {"id": "scopus_csv", "label": "Scopus CSV",      "extensions": ["csv"]},
            {"id": "xlsx",       "label": "Excel",           "extensions": ["xlsx"]},
            {"id": "csv",        "label": "Plain CSV",       "extensions": ["csv"]},
            {"id": "tsv",        "label": "TSV",             "extensions": ["tsv", "txt"]},
        ],
        "target_formats": [
            {"id": "wos",  "label": "WoS plain text",  "extension": "txt"},
            {"id": "vos",  "label": "VOSviewer TSV",   "extension": "txt"},
            {"id": "bib",  "label": "BibTeX",          "extension": "bib"},
            {"id": "ris",  "label": "RIS",             "extension": "ris"},
            {"id": "xlsx", "label": "Excel",           "extension": "xlsx"},
            {"id": "csv",  "label": "CSV",             "extension": "csv"},
            {"id": "tsv",  "label": "TSV",             "extension": "tsv"},
        ],
    }
