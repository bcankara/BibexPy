"""Management of merge analysis runs.

Each merge is stored as a self-contained analysis folder, so previous runs
are preserved, results from different algorithms coexist, the active analysis
can be switched, and snapshot/rollback operations stay safe. Provides path
helpers and CRUD operations over the per-project analyses directory, tracking
the currently active analysis via an active.json pointer.
"""

from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from services import storage


# ─────────────────────────────────────────────────────────────
#  Yol helper'ları
# ─────────────────────────────────────────────────────────────

def analyses_dir(project_id: str) -> Path:
    p = storage.project_dir(project_id) / "analyses"
    p.mkdir(parents=True, exist_ok=True)
    return p


def active_pointer_path(project_id: str) -> Path:
    return analyses_dir(project_id) / "active.json"


def analysis_dir(project_id: str, analysis_id: str) -> Path:
    return analyses_dir(project_id) / analysis_id


# ─────────────────────────────────────────────────────────────
#  CRUD
# ─────────────────────────────────────────────────────────────

def _write_meta(adir: Path, meta: dict[str, Any]) -> None:
    (adir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_meta(adir: Path) -> dict[str, Any]:
    p = adir / "meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def create_analysis(project_id: str, method: str, label: Optional[str] = None) -> tuple[str, Path]:
    """Yeni analiz oluştur — timestamp'li klasör + meta.json. Aktif yapılmaz.

    Dönüş: (analysis_id, path)
    """
    now = time.time()
    stamp = datetime.fromtimestamp(now).strftime("%Y%m%d_%H%M%S")
    analysis_id = f"analysis_{stamp}_{method}"

    # Çakışma durumunda sonek ekle
    base = analyses_dir(project_id)
    i = 1
    while (base / analysis_id).exists():
        analysis_id = f"analysis_{stamp}_{method}_{i}"
        i += 1

    target = analysis_dir(project_id, analysis_id)
    target.mkdir(parents=True, exist_ok=False)

    if not label:
        label = f"{method.title()} — {datetime.fromtimestamp(now).strftime('%d.%m.%Y %H:%M')}"

    meta = {
        "id": analysis_id,
        "created_at": now,
        "method": method,
        "label": label,
        "file_count": 0,
        "source": "user_run",
    }
    _write_meta(target, meta)
    return analysis_id, target


def finalize_analysis(project_id: str, analysis_id: str) -> None:
    """Analiz tamamlandığında file_count'u güncelle + aktif yap."""
    adir = analysis_dir(project_id, analysis_id)
    if not adir.exists():
        return
    meta = _read_meta(adir)
    meta["file_count"] = sum(1 for p in adir.iterdir() if p.is_file() and p.name != "meta.json")
    meta["completed_at"] = time.time()
    _write_meta(adir, meta)
    set_active_analysis(project_id, analysis_id)


def list_analyses(project_id: str) -> list[dict[str, Any]]:
    """Tüm analizleri en yeniden eskiye sırala."""
    out: list[dict[str, Any]] = []
    active_id = get_active_analysis_id(project_id)
    base = analyses_dir(project_id)
    if not base.exists():
        return out

    for p in base.iterdir():
        if not p.is_dir():
            continue
        # Trash klasörlerini (silinmiş ama henüz fiziksel temizlenmemiş) atla
        if p.name.startswith("_deleted_"):
            continue
        meta = _read_meta(p)
        # Soft-delete edilmiş analizleri atla (meta.json'da deleted: true)
        if meta.get("deleted"):
            continue
        if not meta:
            # meta.json yoksa basic info üret
            meta = {
                "id": p.name,
                "created_at": p.stat().st_mtime,
                "method": "unknown",
                "label": p.name,
                "file_count": sum(1 for f in p.iterdir() if f.is_file()),
            }
        meta["is_active"] = (meta.get("id") == active_id)
        # Boyut bilgisi
        try:
            meta["total_size"] = sum(f.stat().st_size for f in p.iterdir() if f.is_file())
        except Exception:
            meta["total_size"] = 0
        out.append(meta)

    out.sort(key=lambda m: m.get("created_at", 0), reverse=True)
    return out


def get_analysis_meta(project_id: str, analysis_id: str) -> Optional[dict[str, Any]]:
    adir = analysis_dir(project_id, analysis_id)
    if not adir.exists():
        return None
    meta = _read_meta(adir)
    # Soft-deleted analizler "yok" gibi davranılır — tekrar silinemez
    if meta.get("deleted"):
        return None
    meta["is_active"] = (meta.get("id") == get_active_analysis_id(project_id))
    return meta or None


def delete_analysis(project_id: str, analysis_id: str) -> bool:
    """Bir analizi sil — Windows-friendly **soft delete** stratejisi.

    Windows'ta açık file handle'lar (pandas read_excel cache, openpyxl iç
    state, vb.) dosyayı `unlink`/`rename` için kilitliyor. Bunu aşmak için:

      1. Aktif analiz siliniyorsa aktifliği başka analize taşı
      2. filter_engine DataFrame cache'i temizle + gc.collect()
      3. **Önce hızlı yol**: shutil.rmtree dene (1 retry).
      4. **Fallback (soft delete)**: meta.json'a `"deleted": true` flag'i yaz.
         list_analyses() bunu filtreler; UI'da görünmez. Disk alanı sonraki
         backend restart'ında `purge_soft_deleted()` ile temizlenir.

    Soft-delete sayesinde DELETE hiçbir zaman 500 fırlatmaz; kullanıcı
    deneyimi tutarlı kalır.
    """
    import gc
    import time as _time

    adir = analysis_dir(project_id, analysis_id)
    if not adir.exists():
        return False

    active_id = get_active_analysis_id(project_id)

    # 1) Aktif analiz siliniyorsa önce aktifliği taşı
    if active_id == analysis_id:
        remaining = [a for a in list_analyses(project_id) if a["id"] != analysis_id]
        if remaining:
            set_active_analysis(project_id, remaining[0]["id"])
        else:
            clear_active_analysis(project_id)

    # 2) Filter cache'i temizle
    try:
        from services import filter_engine
        filter_engine._DF_CACHE.clear()
    except Exception:
        pass
    gc.collect()

    # 3) Hızlı yol: gerçek silme dene (Windows'ta sıklıkla fail eder)
    def _on_rmtree_error(func, path, exc_info):
        try:
            import os, stat
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            raise

    for _ in range(2):
        try:
            shutil.rmtree(adir, onerror=_on_rmtree_error)
            return True
        except OSError:
            gc.collect()
            _time.sleep(0.3)

    # 4) Soft delete fallback — meta.json'a flag yaz
    meta_path = adir / "meta.json"
    try:
        meta = _read_meta(adir)
    except Exception:
        meta = {}
    meta["deleted"] = True
    meta["deleted_at"] = _time.time()
    try:
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except Exception:
        # meta.json bile yazılamıyorsa gerçekten kilitli — başarısız
        if active_id == analysis_id:
            try:
                set_active_analysis(project_id, analysis_id)
            except Exception:
                pass
        return False
    return True


def purge_soft_deleted(project_id: str) -> int:
    """Soft-delete edilmiş ama henüz fiziksel olarak silinmemiş analiz
    klasörlerini temizlemeyi dene. Backend startup'ta veya periyodik
    çağrılabilir. Başarıyla silinen klasör sayısını döner."""
    import gc
    base = analyses_dir(project_id)
    if not base.exists():
        return 0
    purged = 0
    for p in base.iterdir():
        if not p.is_dir():
            continue
        meta = _read_meta(p)
        if not meta.get("deleted"):
            continue
        gc.collect()
        try:
            shutil.rmtree(p, ignore_errors=True)
            if not p.exists():
                purged += 1
        except Exception:
            pass
    return purged


# ─────────────────────────────────────────────────────────────
#  Active analysis
# ─────────────────────────────────────────────────────────────

def get_active_analysis_id(project_id: str) -> Optional[str]:
    p = active_pointer_path(project_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("id")
    except Exception:
        return None


def set_active_analysis(project_id: str, analysis_id: str) -> None:
    p = active_pointer_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"id": analysis_id, "set_at": time.time()}, indent=2), encoding="utf-8")


def clear_active_analysis(project_id: str) -> None:
    p = active_pointer_path(project_id)
    if p.exists():
        p.unlink()


def get_active_analysis_dir(project_id: str) -> Optional[Path]:
    """Aktif analizin klasör yolunu döndür.

    Eğer aktif analiz yoksa, son yapılan analizi otomatik aktif yap.
    Hiç analiz yoksa None döner.
    """
    active_id = get_active_analysis_id(project_id)
    if active_id:
        adir = analysis_dir(project_id, active_id)
        if adir.exists():
            return adir
        # Aktif klasör silinmiş; pointer'ı temizle
        clear_active_analysis(project_id)

    # Aktif yoksa en yenisi
    analyses = list_analyses(project_id)
    if analyses:
        latest = analyses[0]["id"]
        set_active_analysis(project_id, latest)
        return analysis_dir(project_id, latest)

    return None


def work_dir(project_id: str) -> Path:
    """Analiz-spesifik çıktıların (disambiguation önerileri, snapshot'lar, audit)
    yazılacağı klasör = AKTİF analiz klasörü. Böylece her yeni merge (yeni analiz)
    kendi temiz çalışma alanına sahip olur ve eski öneriler/snapshot'lar taşınmaz.

    Hiç analiz yoksa proje köküne düşer (geriye uyumluluk; pratikte merge'siz bu
    yollar çağrılmaz).
    """
    adir = get_active_analysis_dir(project_id)
    return adir if adir is not None else storage.project_dir(project_id)


# ─────────────────────────────────────────────────────────────
#  Dataset path resolver (filter_engine, merger için)
# ─────────────────────────────────────────────────────────────

# Aktif analiz klasöründeki ana dataset dosya adı (Smart Merge → merged.xlsx)
_DATASET_NAMES = (
    "merged.xlsx",
)


def active_dataset_path(project_id: str) -> Optional[Path]:
    """Aktif analizdeki ana birleştirilmiş dataset dosyasını döndür."""
    adir = get_active_analysis_dir(project_id)
    if adir is None:
        return None
    for name in _DATASET_NAMES:
        p = adir / name
        if p.exists():
            return p
    # Fallback: ilk *.xlsx (yan dosyaları atla)
    SKIP = {"statistic.xlsx", "statistic_smart.xlsx", "match_audit.xlsx",
            "conflict_log.xlsx", "borderline_queue.xlsx"}
    for f in sorted(adir.iterdir()):
        if (f.suffix.lower() == ".xlsx"
                and f.name.lower() not in SKIP
                and not f.name.lower().startswith("lost_")):
            return f
    return None
