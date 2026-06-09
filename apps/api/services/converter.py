"""bibex_core dönüşüm fonksiyonlarını web ortamında kullanmak için sarmal.

- Tüm çıktılar `<project>/processed/` altına yazılır
- Stdout/stderr bastırılır (bibex_core print kullanıyor)
- Hatalar HTTPException olarak fırlatılır
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from fastapi import HTTPException

from services import storage
from services.bibex_adapter import _suppress_stdio


def _project_paths(project_id: str) -> tuple[Path, Path, Path]:
    meta = storage.get_project(project_id)
    if meta is None:
        raise HTTPException(404, "Proje bulunamadı")
    root = storage.project_dir(project_id)
    return root, root / "raw", root / "processed"


def _raw_file(raw_dir: Path, name: str) -> Path:
    name = Path(name).name
    p = raw_dir / name
    if not p.exists() or not p.is_file():
        raise HTTPException(404, f"Ham dosya bulunamadı: {name}")
    return p


def csv_to_xlsx(project_id: str, source_names: Iterable[str], output_name: str = "scopus_merged.xlsx") -> Path:
    """Scopus CSV(ler) → tek XLSX."""
    from bibex_core.scp2xlsx import save_to_excel

    _, raw, processed = _project_paths(project_id)
    sources = [str(_raw_file(raw, n)) for n in source_names]
    if not sources:
        raise HTTPException(400, "En az bir CSV dosyası gerekli")
    output = processed / Path(output_name).name
    with _suppress_stdio():
        ok = save_to_excel(sources, str(output))
    if not ok or not output.exists():
        raise HTTPException(500, "CSV → XLSX dönüşümü başarısız")
    storage.touch_project(project_id)
    return output


def wos_to_xlsx(project_id: str, source_names: Iterable[str], output_name: str = "wos_merged.xlsx") -> Path:
    """WoS TXT(ler) → tek XLSX. Birden fazla TXT verilirse önce birleştirir."""
    from bibex_core.wos2xlsx import save_to_excel

    _, raw, processed = _project_paths(project_id)
    sources = [_raw_file(raw, n) for n in source_names]
    if not sources:
        raise HTTPException(400, "En az bir TXT dosyası gerekli")

    # Birleştir
    merged_txt = processed / "_wos_merged.tmp.txt"
    with merged_txt.open("w", encoding="utf-8") as out:
        for src in sources:
            out.write(src.read_text(encoding="utf-8", errors="replace"))
            out.write("\n")

    output = processed / Path(output_name).name
    with _suppress_stdio():
        ok = save_to_excel(str(merged_txt), str(output))
    merged_txt.unlink(missing_ok=True)
    if not ok or not output.exists():
        raise HTTPException(500, "WoS → XLSX dönüşümü başarısız")
    storage.touch_project(project_id)
    return output


def auto_prepare(project_id: str, ctx=None) -> dict:
    """Ham CSV/TXT'leri konsolide `processed/*.xlsx`'e çevirir — ÖRTÜK hazırlık.

    Smart Merge job'unun ilk fazı olarak çağrılır; ayrı "Prepare" adımı yok.
    Skip-if-fresh: bir kaynağı yalnız en yeni ham dosyası ilgili processed
    XLSX'ten yeni ise (veya XLSX yoksa) yeniden çevirir. Hata FIRLATMAZ —
    sorunları `skipped`'a ekler ki tek-kaynak merge yine yürüyebilsin.

    ctx: opsiyonel JobContext benzeri (.log()/.progress()); tip bağımlılığı
    eklememek için gevşek bırakıldı.
    """
    _, raw, processed = _project_paths(project_id)
    report: dict = {
        "scopus_xlsx": None, "wos_xlsx": None,
        "csv": 0, "txt": 0, "skipped": [], "did_convert": False,
    }
    if not raw.exists():
        return report

    csv_files = [f for f in raw.iterdir() if f.is_file() and f.suffix.lower() == ".csv"]
    txt_files = [f for f in raw.iterdir() if f.is_file() and f.suffix.lower() in (".txt", ".isi")]
    report["csv"] = len(csv_files)
    report["txt"] = len(txt_files)

    def _fresh(out_name: str, raw_paths: list[Path]) -> bool:
        out = processed / out_name
        if not out.exists():
            return False
        try:
            newest_raw = max((p.stat().st_mtime for p in raw_paths), default=0.0)
            return out.stat().st_mtime >= newest_raw
        except OSError:
            return False

    def _convert(kind: str, out_name: str, legacy: str, raw_paths: list[Path], fn) -> None:
        if _fresh(out_name, raw_paths):
            report[f"{kind}_xlsx"] = out_name
            if ctx:
                ctx.log(f"{kind.capitalize()} already prepared (up to date)")
            return
        old = processed / legacy
        if old.exists():
            try:
                old.unlink()
            except OSError:
                pass
        try:
            if ctx:
                ctx.log(f"Preparing {kind} — {len(raw_paths)} file(s) → {out_name}")
            out = fn(project_id, [p.name for p in raw_paths], out_name)
            report[f"{kind}_xlsx"] = out.name
            report["did_convert"] = True
        except Exception as e:  # noqa: BLE001 — tek-kaynak merge yürüsün diye yutulur
            report["skipped"].append(f"{kind}: {e}")

    if csv_files:
        _convert("scopus", "scopus.xlsx", "scopus_merged.xlsx", csv_files, csv_to_xlsx)
    if ctx:
        ctx.progress(0.12)
    if txt_files:
        _convert("wos", "wos.xlsx", "wos_merged.xlsx", txt_files, wos_to_xlsx)
    if ctx:
        ctx.progress(0.18)

    return report


def xlsx_to_wos_txt(project_id: str, source_name: str, output_name: str | None = None) -> Path:
    """XLSX → WoS-format TXT (bibliometrix için)."""
    from bibex_core.xlsx2vos import convert_excel_to_wos

    _, _, processed = _project_paths(project_id)
    src = processed / Path(source_name).name
    if not src.exists():
        # raw'da da bakalım, kullanıcı doğrudan yüklediyse
        raw = storage.project_dir(project_id) / "raw"
        alt = raw / Path(source_name).name
        if alt.exists():
            src = alt
        else:
            raise HTTPException(404, f"XLSX bulunamadı: {source_name}")

    output = processed / (output_name or (src.stem + "_wos.txt"))
    with _suppress_stdio():
        convert_excel_to_wos(str(src), str(output))
    if not output.exists():
        raise HTTPException(500, "XLSX → WoS TXT dönüşümü başarısız")
    storage.touch_project(project_id)
    return output


def xlsx_to_tsv(project_id: str, source_name: str, output_name: str | None = None, sep: str = "\t") -> Path:
    """XLSX → düz TSV/CSV (pandas)."""
    import pandas as pd

    _, _, processed = _project_paths(project_id)
    src = processed / Path(source_name).name
    if not src.exists():
        raw = storage.project_dir(project_id) / "raw"
        alt = raw / Path(source_name).name
        if alt.exists():
            src = alt
        else:
            raise HTTPException(404, f"XLSX bulunamadı: {source_name}")

    ext = "tsv" if sep == "\t" else "csv"
    output = processed / (output_name or (src.stem + f".{ext}"))
    df = pd.read_excel(src)
    df.to_csv(output, sep=sep, index=False, encoding="utf-8")
    storage.touch_project(project_id)
    return output


def list_processed(project_id: str) -> list[dict]:
    """processed/ klasöründeki dosyaları listele."""
    _, _, processed = _project_paths(project_id)
    if not processed.exists():
        return []
    out = []
    for f in sorted(processed.iterdir()):
        if not f.is_file():
            continue
        out.append({
            "name": f.name,
            "size": f.stat().st_size,
            "relative_path": str(f.relative_to(storage.settings.storage_path)),
        })
    return out
