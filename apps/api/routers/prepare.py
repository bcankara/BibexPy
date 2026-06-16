"""Data preparation orchestration endpoints.

Exposes API routes that automatically convert raw uploaded CSV/TXT
files into merged XLSX outputs, report preparation status (including
staleness), reset preparation, and delete individual processed files.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import audit, converter, storage

router = APIRouter(prefix="/projects/{project_id}/prepare", tags=["prepare"])


class ProcessedFile(BaseModel):
    name: str
    size: int
    mtime: float
    kind: str  # "scopus" | "wos" | "other"


class PrepareReport(BaseModel):
    scopus_xlsx: str | None = None
    wos_xlsx: str | None = None
    raw_csv_count: int = 0
    raw_txt_count: int = 0
    skipped: list[str] = []
    messages: list[str] = []
    # Tüm processed/ dosyaları detayıyla (UI'da liste için)
    processed_files: list[ProcessedFile] = []
    # Hazırlığın güncelliği — bir raw dosya processed'tan yeni ise stale
    stale: bool = False


def _classify_processed(name: str) -> str:
    n = name.lower()
    if "scopus" in n or "scp" in n:
        return "scopus"
    if "wos" in n:
        return "wos"
    return "other"


@router.get("/status", response_model=PrepareReport)
def status(project_id: str):
    """Hazırlık durumunu döndürür — hangi XLSX'ler hazır, hangi ham dosyalar var."""
    meta = storage.get_project(project_id)
    if meta is None:
        raise HTTPException(404, "project_not_found")
    raw = storage.project_dir(project_id) / "raw"
    processed = storage.project_dir(project_id) / "processed"

    report = PrepareReport()
    raw_max_mtime = 0.0
    if raw.exists():
        for f in raw.iterdir():
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            if ext == ".csv":
                report.raw_csv_count += 1
            elif ext in (".txt", ".isi"):
                report.raw_txt_count += 1
            raw_max_mtime = max(raw_max_mtime, f.stat().st_mtime)

    processed_min_mtime = float("inf")
    if processed.exists():
        for f in sorted(processed.iterdir()):
            if not f.is_file():
                continue
            st = f.stat()
            kind = _classify_processed(f.name)
            report.processed_files.append(ProcessedFile(
                name=f.name,
                size=st.st_size,
                mtime=st.st_mtime,
                kind=kind,
            ))
            if kind == "scopus" and report.scopus_xlsx is None:
                report.scopus_xlsx = f.name
            elif kind == "wos" and report.wos_xlsx is None:
                report.wos_xlsx = f.name
            processed_min_mtime = min(processed_min_mtime, st.st_mtime)

    # Stale: en yeni raw dosya, en eski processed dosyadan daha sonra ise hazırlık güncel değil
    if report.processed_files and raw_max_mtime > 0:
        report.stale = raw_max_mtime > processed_min_mtime
    return report


@router.post("", response_model=PrepareReport)
def prepare(project_id: str):
    """Tüm ham dosyaları otomatik XLSX'e çevirir.

    - Tüm Scopus CSV'leri → `scopus_merged.xlsx`
    - Tüm WoS TXT'leri → `wos_merged.xlsx`
    """
    meta = storage.get_project(project_id)
    if meta is None:
        raise HTTPException(404, "project_not_found")

    raw = storage.project_dir(project_id) / "raw"
    if not raw.exists():
        raise HTTPException(400, "no_raw_files")

    csv_files = [f.name for f in raw.iterdir() if f.suffix.lower() == ".csv"]
    txt_files = [f.name for f in raw.iterdir() if f.suffix.lower() in (".txt", ".isi")]

    report = PrepareReport(raw_csv_count=len(csv_files), raw_txt_count=len(txt_files))

    if not csv_files and not txt_files:
        raise HTTPException(400, "no_files_to_process")

    if csv_files:
        try:
            # Önce eski adlandırma (scopus_merged.xlsx) varsa temizle — yenisini scopus.xlsx olarak yazacağız
            old_scp = storage.project_dir(project_id) / "processed" / "scopus_merged.xlsx"
            if old_scp.exists():
                try:
                    old_scp.unlink()
                except Exception:
                    pass
            out = converter.csv_to_xlsx(project_id, csv_files, "scopus.xlsx")
            report.scopus_xlsx = out.name
            report.messages.append(f"Scopus: {len(csv_files)} CSV → {out.name}")
        except Exception as e:
            report.skipped.append(f"Scopus CSV dönüşümü: {e}")

    if txt_files:
        try:
            old_wos = storage.project_dir(project_id) / "processed" / "wos_merged.xlsx"
            if old_wos.exists():
                try:
                    old_wos.unlink()
                except Exception:
                    pass
            out = converter.wos_to_xlsx(project_id, txt_files, "wos.xlsx")
            report.wos_xlsx = out.name
            report.messages.append(f"WoS: {len(txt_files)} TXT → {out.name}")
        except Exception as e:
            report.skipped.append(f"WoS TXT dönüşümü: {e}")

    audit.write(
        project_id,
        kind="convert",
        title=f"Otomatik hazırlık — {len(csv_files)} CSV + {len(txt_files)} TXT",
        title_key="audit.titles.autoPrepare",
        title_params={"csv": len(csv_files), "txt": len(txt_files)},
        details={
            "csv_files": len(csv_files),
            "txt_files": len(txt_files),
            "scopus_xlsx": report.scopus_xlsx,
            "wos_xlsx": report.wos_xlsx,
            "messages": report.messages,
            "skipped": report.skipped,
        },
        user_action="auto_prepare",
    )
    return report


class ResetResult(BaseModel):
    ok: bool = True
    deleted_processed: int = 0


@router.post("/reset", response_model=ResetResult)
def reset(project_id: str):
    """Veri hazırlığını sıfırla.

    Kullanıcının "Yeni veri ekle" akışı: sadece hazırlanmış birleşik Excel
    dosyaları (`processed/scopus_merged.xlsx`, `processed/wos_merged.xlsx`)
    silinir. Ham yüklenen dosyalar (`raw/`) **dokunulmaz** — kullanıcı bunlara
    yeni dosyalar ekleyip tekrar hazırlayacak. Analiz klasörleri (`analyses/`)
    da silinmez; geçmiş birleştirmeler korunur.
    """
    meta = storage.get_project(project_id)
    if meta is None:
        raise HTTPException(404, "project_not_found")

    root = storage.project_dir(project_id)
    processed = root / "processed"

    deleted_processed = 0

    if processed.exists():
        for f in list(processed.iterdir()):
            if f.is_file():
                try:
                    f.unlink()
                    deleted_processed += 1
                except Exception:
                    pass

    audit.write(
        project_id,
        kind="upload",
        title=f"Hazırlık sıfırlandı — {deleted_processed} işlenmiş dosya silindi (ham dosyalar korundu)",
        title_key="audit.titles.prepareReset",
        title_params={"n": deleted_processed},
        details={
            "deleted_processed_files": deleted_processed,
        },
        user_action="prepare_reset",
    )

    return ResetResult(ok=True, deleted_processed=deleted_processed)


def _safe_name(name: str) -> str:
    from pathlib import Path as _P
    base = _P(name).name
    if not base or base in {".", ".."}:
        raise HTTPException(400, f"invalid_filename: {name!r}")
    return base


@router.delete("/processed/{filename}", status_code=204)
def delete_processed_file(project_id: str, filename: str):
    """Tek bir hazırlanmış dosyayı sil. Ham dosyalar ve analiz klasörleri korunur."""
    meta = storage.get_project(project_id)
    if meta is None:
        raise HTTPException(404, "project_not_found")
    name = _safe_name(filename)
    target = storage.project_dir(project_id) / "processed" / name
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "file_not_found")
    size = target.stat().st_size
    target.unlink()
    audit.write(
        project_id,
        kind="upload",
        title=f"Hazırlanmış dosya silindi: {name}",
        title_key="audit.titles.processedFileDeleted",
        title_params={"name": name},
        details={"name": name, "size": size, "folder": "processed", "action": "delete"},
        user_action="delete_processed_file",
    )
    return None
