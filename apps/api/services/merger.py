"""Merge operations wrapper.

Each merge is stored as an independent analysis folder. This module loads the
source datasets, handles the single-source passthrough case (no deduplication),
and dispatches two-source merges to the smart_merger pipeline, which opens and
finalizes its own analysis folder.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import HTTPException

from jobs.runner import JobContext
from services import analyses, converter, dataset_io, storage


def _project_paths(project_id: str) -> tuple[Path, Path, Path]:
    """(root, raw, processed) — proje veri klasörleri."""
    meta = storage.get_project(project_id)
    if meta is None:
        raise HTTPException(404, "Proje bulunamadı")
    root = storage.project_dir(project_id)
    return root, root / "raw", root / "processed"


def _find_xlsx(processed: Path, raw: Path, hint: str) -> Path | None:
    """Önce processed, sonra raw'da ara."""
    for d in (processed, raw):
        if not d.exists():
            continue
        for f in d.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() == ".xlsx" and hint in f.name.lower():
                return f
    return None


def check_ready_to_merge(project_id: str) -> None:
    """Merge başlamadan ÖNCE çağrılır (job submit etmeden). Hazırlık artık merge
    job'unun ilk fazında ÖRTÜK yapıldığından ham veri yeterlidir; yalnızca hiç
    kaynak veri yoksa 409 'no_source_data' fırlatır (UI kullanıcıyı yüklemeye yönlendirir).
    """
    _, raw, processed = _project_paths(project_id)
    has_xlsx = bool(
        _find_xlsx(processed, raw, "scopus") or _find_xlsx(processed, raw, "scp")
        or _find_xlsx(processed, raw, "wos")
    )
    has_raw = raw.exists() and any(
        f.is_file() and f.suffix.lower() in (".csv", ".txt", ".isi", ".xlsx")
        for f in raw.iterdir()
    )
    if has_xlsx or has_raw:
        return
    raise HTTPException(409, "no_source_data")


def _input_paths(project_id: str) -> tuple[Path | None, Path | None]:
    """Konsolide Scopus/WoS XLSX yollarını çöz (YÜKLEMEDEN — varlık tespiti için)."""
    _, raw, processed = _project_paths(project_id)
    scp_xlsx = _find_xlsx(processed, raw, "scopus") or _find_xlsx(processed, raw, "scp")
    wos_xlsx = _find_xlsx(processed, raw, "wos")
    return scp_xlsx, wos_xlsx


def _load_inputs(
    project_id: str, ctx: JobContext | None = None
) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """Scopus + WoS XLSX'leri yükle (hazırlık auto_prepare'de çoktan yapılmış olmalı).

    Tek kaynak varsa onu döndürür, diğeri None olur (birleştirme adımı bunu
    passthrough yapar). YALNIZCA ikisi de yoksa hata verir — kullanıcı sadece
    Scopus ya da sadece WoS verisini hazırlamak isteyebilir.
    """
    _, raw, processed = _project_paths(project_id)

    scp_xlsx, wos_xlsx = _input_paths(project_id)

    # Hazırlanmış (processed) XLSX yoksa merge ham CSV/TXT'den OTOMATİK üretmez —
    # bu 1. adımı (Preparing) baypas ederdi. Bunun yerine, ham veri var ama hazırlık
    # yapılmamışsa kullanıcıyı 1. adıma yönlendiren net bir hata döndürürüz.
    if scp_xlsx is None and wos_xlsx is None:
        has_raw = raw.exists() and any(
            f.suffix.lower() in (".csv", ".txt", ".isi", ".xlsx")
            for f in raw.iterdir() if f.is_file()
        )
        # Ham veri VAR ama hazırlanmamış → "önce hazırla"; hiç veri yoksa → "önce yükle".
        raise HTTPException(409, "not_prepared" if has_raw else "no_source_data")

    scp_df = pd.read_excel(scp_xlsx) if scp_xlsx is not None else None
    wos_df = pd.read_excel(wos_xlsx) if wos_xlsx is not None else None
    if ctx:
        ctx.log(
            f"Sources — Scopus: {len(scp_df) if scp_df is not None else 'none'}, "
            f"WoS: {len(wos_df) if wos_df is not None else 'none'}"
        )
    return scp_df, wos_df


async def _run_single_source(
    ctx: JobContext, project_id: str,
    scp_df: pd.DataFrame | None, wos_df: pd.DataFrame | None,
) -> dict[str, Any]:
    """Tek kaynak (yalnız Scopus veya yalnız WoS) — birleştirilecek ikinci veri yok.
    Tek kaynağı doğrudan analiz veri seti yapar (dedup yok), filtreye hazır.
    """
    import asyncio
    df = scp_df if scp_df is not None else wos_df
    src = "Scopus" if scp_df is not None else "WoS"
    ctx.log(f"Single source ({src}) — no second dataset to merge, used directly (no dedup).")
    ctx.progress(0.3)

    analysis_id, adir = analyses.create_analysis(project_id, "single")
    ctx.log(f"Analysis folder: {analysis_id}")
    output_dataset = adir / "merged.parquet"
    await asyncio.to_thread(dataset_io.atomic_write_dataset, df, output_dataset)
    ctx.progress(0.7)

    scp_count = int(len(scp_df)) if scp_df is not None else 0
    wos_count = int(len(wos_df)) if wos_df is not None else 0
    statistic_xlsx = adir / "Statistic.xlsx"
    try:
        from services.smart_merger import _write_statistic_smart
        await asyncio.to_thread(
            _write_statistic_smart, int(len(df)), wos_count, scp_count, df, statistic_xlsx,
        )
    except Exception as e:
        ctx.log(f"Statistic.xlsx could not be generated: {e}")

    try:
        from services import filter_engine
        filter_engine._DF_CACHE.clear()
    except Exception:
        pass

    analyses.finalize_analysis(project_id, analysis_id)
    storage.touch_project(project_id)
    ctx.progress(1.0)
    ctx.log(f"Single source ready — {len(df)} records. You can proceed to the filtering step.")
    return {
        "method": "single",
        "analysis_id": analysis_id,
        "scopus_input": scp_count,
        "wos_input": wos_count,
        "merged_count": int(len(df)),
        "duplicates_removed": 0,
        "output_dataset": str(output_dataset.relative_to(storage.settings.storage_path)),
        "stats": {},
    }


class _ScaledJobContext:
    """Alt-fazın 0-1 ilerlemesini üst işin [lo, hi] aralığına haritalar.

    Hazırlık (auto_prepare) çubuğu %18'e kadar getiriyor; Smart Merge kendi
    ölçeğinde 0.02'den başlıyordu — sarmalanmadan ilerleme 12-18%'den 2%'ye
    GERİ sarıyor ve iş donmuş gibi görünüyordu. log/cancelled aynen delege.
    """

    def __init__(self, ctx: JobContext, lo: float, hi: float):
        self._ctx, self._lo, self._hi = ctx, lo, hi

    def log(self, line: str) -> None:
        self._ctx.log(line)

    def progress(self, value: float) -> None:
        v = max(0.0, min(1.0, value))
        self._ctx.progress(self._lo + v * (self._hi - self._lo))

    @property
    def cancelled(self) -> bool:
        return self._ctx.cancelled


async def run_merge(ctx: JobContext, project_id: str) -> dict[str, Any]:
    """Birleştirme jobu — tek algoritma: Smart Merge.

    Tek kaynak (yalnız Scopus veya yalnız WoS) varsa passthrough (dedup yok);
    iki kaynak varsa bağımsız smart_merger pipeline'ı çalışır — kendi analiz
    klasörünü açar, finalize eder ve özetini döndürür.
    """
    import asyncio

    ctx.log("Starting merge (Smart Merge)")
    ctx.progress(0.05)

    # Örtük hazırlık — ham CSV/TXT'leri konsolide XLSX'e çevir (ayrı Prepare adımı yok).
    # Skip-if-fresh: yalnız ham dosya processed'tan yeniyse yeniden çevirir.
    ctx.log("Preparing sources (CSV/TXT → XLSX)")
    await asyncio.to_thread(converter.auto_prepare, project_id, ctx)

    # Tek-kaynak tespiti DOSYA VARLIĞIYLA yapılır (yüklemeden): iki kaynak
    # varken frame'leri burada da yüklemek, run_smart_merge kendi _load_inputs
    # çağrısını yaparken korpusun bellekte İKİNCİ bir kopyasını canlı tutuyordu
    # (büyük korpuslarda yüzlerce MB gereksiz tepe bellek).
    scp_xlsx, wos_xlsx = _input_paths(project_id)

    # Tek kaynak (ya da hiçbiri): _load_inputs yükler; ikisi de yoksa 409'u o verir.
    if scp_xlsx is None or wos_xlsx is None:
        scp_df, wos_df = await asyncio.to_thread(_load_inputs, project_id, ctx)
        return await _run_single_source(ctx, project_id, scp_df, wos_df)

    # İki kaynak da var → Smart Merge (bağımsız pipeline, kendi klasörünü açar
    # ve kaynakları KENDİSİ yükler — tek kopya). İlerlemesi hazırlığın bittiği
    # %18'den itibaren ölçeklenir ki çubuk geri sarmasın.
    from services import smart_merger
    return await smart_merger.run_smart_merge(_ScaledJobContext(ctx, 0.18, 1.0), project_id)


def list_merged(project_id: str) -> list[dict]:
    """Aktif analiz klasöründeki dosyaları listele."""
    adir = analyses.get_active_analysis_dir(project_id)
    if adir is None:
        return []
    out = []
    for f in sorted(adir.iterdir()):
        if not f.is_file() or f.name == "meta.json":
            continue
        if f.name.endswith(".tmp~"):
            continue
        out.append({
            "name": f.name,
            "size": f.stat().st_size,
            "relative_path": str(f.relative_to(storage.settings.storage_path)),
        })
    return out


def merged_dataset_path(project_id: str) -> Path | None:
    """Filter/export için ana birleştirilmiş dataset'i bul (merged.parquet).

    Aktif analiz klasöründen okur. Records / Filter / Quality / Disambiguation
    otomatik olarak aktif analizin dataset'ini görür.
    """
    return analyses.active_dataset_path(project_id)
