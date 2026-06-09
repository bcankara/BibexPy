import json
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from jobs.runner import job_runner
from services import analyses, merger, storage

router = APIRouter(prefix="/projects/{project_id}/merge", tags=["merge"])


class MergeRequest(BaseModel):
    # Tek algoritma: Smart Merge. Alan geriye uyumluluk için duruyor ama yok sayılır.
    method: str = "smart"


@router.post("")
async def start_merge(project_id: str, payload: MergeRequest | None = None):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    # Tek algoritma: Smart Merge — payload.method artık yok sayılır.
    # Hazırlık kontrolü — job submit ETMEDEN önce; hazırlanmış XLSX yoksa 409 (UI
    # kullanıcıyı 1. adıma yönlendirir). Böylece merge ham CSV/TXT'yi sessizce kullanmaz.
    merger.check_ready_to_merge(project_id)

    async def worker(ctx):
        return await merger.run_merge(ctx, project_id)

    job = job_runner.submit(
        project_id=project_id,
        kind="merge",
        title="Birleştirme (Smart Merge)",
        title_key="audit.titles.merge",
        title_params={"method": "smart"},
        worker=worker,
    )
    return {"job_id": job.id}


@router.get("/results")
def list_merged(project_id: str):
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    return merger.list_merged(project_id)


# Alan kodları → açıklayıcı Türkçe etiket
FIELD_LABELS = {
    "TI": "Başlık", "AU": "Yazarlar (kısa)", "AF": "Yazarlar (tam)",
    "SO": "Dergi/Kaynak", "JI": "Dergi (ISO)", "J9": "Dergi (kısa)",
    "PY": "Yıl", "TC": "Atıf sayısı", "DT": "Doküman tipi", "LA": "Dil",
    "DI": "DOI", "AB": "Özet", "DE": "Anahtar Kelimeler", "ID": "Keywords Plus",
    "C1": "Adresler", "RP": "İletişim yazarı", "EM": "E-posta",
    "CR": "Referanslar", "NR": "Ref. sayısı", "PG": "Sayfa", "PU": "Yayıncı",
    "WC": "WoS Kategorisi", "SC": "Konu Kategorisi", "DB": "Kaynak DB",
    "UT": "WoS ID", "PM": "PubMed ID", "VL": "Cilt", "IS": "Sayı",
    "BP": "Başlangıç sayfa", "EP": "Bitiş sayfa", "OA": "Açık Erişim",
    "SR": "Source-Year ref",
}


def _merge_is_stale(project_id: str, meta: dict[str, Any] | None) -> bool:
    """Ham (raw) veriler aktif merge'den SONRA eklendiyse/değiştiyse True.

    Hazırlık artık merge içinde örtük yapıldığından, kullanıcı yeni ham dosya
    eklediğinde raw/*.{csv,txt,isi} mtime'ı merge zamanını geçer → aktif merge
    eski veriye dayanır. UI bunu "Eski Merge" olarak işaretleyip kullanıcıyı
    yeniden Smart Merge'e yönlendirir.
    """
    if not meta:
        return False
    merge_ts = meta.get("completed_at") or meta.get("created_at")
    if not merge_ts:
        return False
    try:
        raw = storage.project_dir(project_id) / "raw"
        if not raw.exists():
            return False
        newest = 0.0
        for pat in ("*.csv", "*.txt", "*.isi"):
            for f in raw.glob(pat):
                try:
                    newest = max(newest, f.stat().st_mtime)
                except OSError:
                    continue
        # 2 sn tolerans: merge'in completed_at'i ile son yüklemenin mtime'ı
        # arasındaki küçük farktan ötürü yanlış-pozitif olmasın.
        return bool(newest and newest > float(merge_ts) + 2.0)
    except Exception:
        return False


@router.get("/summary")
def merge_summary(project_id: str) -> dict[str, Any]:
    """Aktif analizin detaylı istatistikleri.

    has_merge = aktif bir analiz klasörünün varlığı. Statistic.xlsx olsa da
    olmasa da has_merge=True döner; Statistic yoksa sadece `general` boş kalır,
    `files` listesi yine doldurulur.
    """
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")

    # Aktif analiz klasörü
    merged_dir = analyses.get_active_analysis_dir(project_id)
    if merged_dir is None or not merged_dir.exists():
        return {"has_merge": False}

    out: dict[str, Any] = {"has_merge": True}
    # Analiz meta'sını ek (Statistic'ten bağımsız — analiz varsa hep döner)
    meta = analyses.get_analysis_meta(project_id, merged_dir.name)
    if meta:
        out["analysis"] = {
            "id": meta.get("id"),
            "label": meta.get("label"),
            "method": meta.get("method"),
            "created_at": meta.get("created_at"),
            "completed_at": meta.get("completed_at"),
            "is_active": meta.get("is_active"),
        }

    # Bayatlık: işlenmiş veriler bu merge'den sonra yeniden hazırlandıysa
    # (yeni dosya + tekrar prepare) aktif merge eski veriye dayanır.
    out["stale"] = _merge_is_stale(project_id, meta)

    # Statistic.xlsx tercih edilir; geriye uyumluluk için Statistic_Smart de denenir
    stats_xlsx = merged_dir / "Statistic.xlsx"
    if not stats_xlsx.exists():
        stats_xlsx_smart = merged_dir / "Statistic_Smart.xlsx"
        if stats_xlsx_smart.exists():
            stats_xlsx = stats_xlsx_smart

    # 1) Genel istatistikler — önce Statistic.xlsx'ten oku, yoksa merged.xlsx + lost'tan hesapla
    if stats_xlsx.exists():
        try:
            gen = pd.read_excel(stats_xlsx, sheet_name="General Stats")
            if len(gen) > 0:
                row = gen.iloc[0]
                out["general"] = {
                    "total_records": int(row.get("Total Records", 0)),
                    "wos_records": int(row.get("WoS Records", 0)),
                    "scopus_records": int(row.get("Scopus Records", 0)),
                    "merged_columns": int(row.get("Merged Columns", 0)),
                    "common_columns": int(row.get("Common Columns", 0)),
                }
                wos = out["general"]["wos_records"]
                scopus = out["general"]["scopus_records"]
                total_in = wos + scopus
                merged = out["general"]["total_records"]
                out["general"]["duplicates_removed"] = max(0, total_in - merged)
                out["general"]["total_input"] = total_in
                out["general"]["dedup_rate"] = round((total_in - merged) / total_in, 4) if total_in else 0
        except Exception:
            pass

    # Fallback: Statistic.xlsx yok / okunamadı → merged.xlsx'ten hesapla
    if "general" not in out:
        try:
            from services import merger
            dataset = merger.merged_dataset_path(project_id)
            if dataset and dataset.exists():
                df = pd.read_excel(dataset)
                total_records = int(len(df))
                merged_columns = int(len(df.columns))
                out["general"] = {
                    "total_records": total_records,
                    "wos_records": 0,
                    "scopus_records": 0,
                    "merged_columns": merged_columns,
                    "common_columns": 0,
                    "duplicates_removed": 0,
                    "total_input": total_records,
                    "dedup_rate": 0,
                }
        except Exception:
            pass

    # 2) Alan-bazlı istatistikler
    if stats_xlsx.exists():
        try:
            fs = pd.read_excel(stats_xlsx, sheet_name="Field Stats")
            field_col = fs.columns[0]
            fields = []
            total = out.get("general", {}).get("total_records", 0)
            for _, r in fs.iterrows():
                code = str(r[field_col]).strip()
                if not code or code.lower() == "nan":
                    continue
                missing = int(r.get("Missing Count", 0))
                pct = float(r.get("Missing %", 0))
                status = str(r.get("Status", ""))
                fields.append({
                    "field": code,
                    "label": FIELD_LABELS.get(code, code),
                    "total": total,
                    "missing": missing,
                    "filled": total - missing,
                    "missing_pct": round(pct, 2),
                    "fill_rate": round((total - missing) / total, 4) if total else 0,
                    "status": status,
                })
            out["fields"] = fields
        except Exception:
            out["fields"] = []
    else:
        out["fields"] = []

    # 3) Dosyalar — ana çıktı + yan dosyalar
    files = []
    lost_wos = 0
    lost_scopus = 0
    for f in sorted(merged_dir.iterdir()):
        if not f.is_file() or f.name == "meta.json":
            continue
        kind = "other"
        name_lower = f.name.lower()
        if name_lower in ("merged.xlsx",) or ("merged" in name_lower and f.suffix.lower() == ".xlsx"):
            kind = "merged_dataset"
        elif name_lower.startswith("lost_wos"):
            kind = "lost_wos"
            try:
                lost_wos = int(len(pd.read_excel(f)))
            except Exception:
                pass
        elif name_lower.startswith("lost_scopus"):
            kind = "lost_scopus"
            try:
                lost_scopus = int(len(pd.read_excel(f)))
            except Exception:
                pass
        elif name_lower.startswith("statistic"):
            kind = "statistics"
        elif name_lower == "match_audit.xlsx":
            kind = "match_audit"
        elif name_lower == "conflict_log.xlsx":
            kind = "conflict_log"
        elif name_lower == "borderline_queue.xlsx":
            kind = "borderline_queue"
        files.append({
            "name": f.name,
            "size": f.stat().st_size,
            "mtime": f.stat().st_mtime,
            "kind": kind,
            "relative_path": str(f.relative_to(storage.settings.storage_path)),
        })
    out["files"] = files
    out["lost_wos_count"] = lost_wos
    out["lost_scopus_count"] = lost_scopus

    # 4) Method — tek algoritma Smart Merge. meta.json'da varsa onu kullan
    #    ("single" tek-kaynak için), yoksa smart varsay.
    method = (meta.get("method") if meta else None) or "smart"
    out["method"] = method

    # 5) Smart-özel istatistikler
    if method == "smart":
        # match_audit.xlsx → stage dağılımı
        audit_xlsx = merged_dir / "match_audit.xlsx"
        if audit_xlsx.exists():
            try:
                audit_df = pd.read_excel(audit_xlsx)
                if "stage_label" in audit_df.columns:
                    stage_counts = audit_df["stage_label"].value_counts().to_dict()
                    out["match_stages"] = {str(k): int(v) for k, v in stage_counts.items()}
            except Exception:
                out["match_stages"] = {}

        # conflict_log.xlsx → çakışma sayısı
        conflict_xlsx = merged_dir / "conflict_log.xlsx"
        if conflict_xlsx.exists():
            try:
                cdf = pd.read_excel(conflict_xlsx)
                out["conflict_count"] = int(len(cdf))
                if "chosen_source" in cdf.columns:
                    src_dist = cdf["chosen_source"].value_counts().to_dict()
                    out["field_source_distribution"] = {str(k): int(v) for k, v in src_dist.items()}
            except Exception:
                out["conflict_count"] = 0

        # borderline state'ten pending sayısı
        state_path = merged_dir / "borderline_state.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                out["borderline_pending"] = sum(1 for v in state.values() if v.get("status") == "pending")
                out["borderline_total"] = len(state)
            except Exception:
                out["borderline_pending"] = 0
                out["borderline_total"] = 0
        else:
            out["borderline_pending"] = 0
            out["borderline_total"] = 0

    return out


# ────────────────────────────────────────────────────────────────────
#  Smart Merge — Borderline review endpoint'leri
# ────────────────────────────────────────────────────────────────────

@router.get("/borderline")
def list_borderline(project_id: str):
    """Smart Merge'in borderline kuyruğunu listele (manuel onay için)."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    from services import smart_merger
    return smart_merger.list_borderline(project_id)


class BorderlineDecision(BaseModel):
    pair_id: str
    decision: str  # "accept" | "reject" | "skip"


class BorderlineDecidePayload(BaseModel):
    decisions: list[BorderlineDecision] = Field(default_factory=list)


@router.post("/borderline/decide")
def decide_borderline(project_id: str, payload: BorderlineDecidePayload):
    """Kullanıcının borderline kararlarını uygula. Snapshot alır, audit yazar."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    from services import smart_merger
    decisions = [d.model_dump() for d in payload.decisions]
    if not decisions:
        raise HTTPException(400, "decisions_empty")
    valid = {"accept", "reject", "skip"}
    for d in decisions:
        if d["decision"] not in valid:
            raise HTTPException(400, f"invalid_decision: {d['decision']}")
    return smart_merger.decide_borderline(project_id, decisions)


# ────────────────────────────────────────────────────────────────────
#  Analiz yönetimi — her birleştirme bağımsız analiz klasörüdür
# ────────────────────────────────────────────────────────────────────

@router.get("/analyses")
def list_analyses_endpoint(project_id: str):
    """Bu projedeki tüm analizleri en yeniden eskiye listele.

    Her item: {id, label, method, created_at, completed_at?, file_count, total_size, is_active}
    """
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    return {
        "active_id": analyses.get_active_analysis_id(project_id),
        "items": analyses.list_analyses(project_id),
    }


@router.post("/analyses/{analysis_id}/activate")
def activate_analysis(project_id: str, analysis_id: str):
    """Bir analizi aktif yap — Records/Filter/Quality bundan sonra onun üzerinde çalışır."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    meta = analyses.get_analysis_meta(project_id, analysis_id)
    if not meta:
        raise HTTPException(404, "analysis_not_found")
    analyses.set_active_analysis(project_id, analysis_id)
    # Filter cache invalidate (aktif dataset değişti)
    try:
        from services import filter_engine
        filter_engine._DF_CACHE.clear()
    except Exception:
        pass
    # Audit
    try:
        from services import audit
        audit.write(
            project_id,
            kind="analysis_activate",
            title=f"Aktif analiz: {meta.get('label') or analysis_id}",
            title_key="audit.titles.analysisActivated",
            title_params={"label": meta.get("label") or analysis_id},
            details={
                "analysis_id": analysis_id,
                "method": meta.get("method"),
                "label": meta.get("label"),
            },
            user_action="analysis_activate",
        )
    except Exception:
        pass
    return {"ok": True, "active_id": analysis_id}


@router.delete("/analyses/{analysis_id}")
def delete_analysis_endpoint(project_id: str, analysis_id: str):
    """Bir analizi sil. Windows'ta dosya kilidi varsa soft-delete'e düşer
    (UI'da görünmez ama dosyalar diskte). Aktifse aktiflik en yeni başka
    analize geçer."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    meta = analyses.get_analysis_meta(project_id, analysis_id)
    if not meta:
        raise HTTPException(404, "analysis_not_found")
    ok = analyses.delete_analysis(project_id, analysis_id)
    if not ok:
        raise HTTPException(500, "analysis_delete_failed")
    # Cache temizle
    try:
        from services import filter_engine
        filter_engine._DF_CACHE.clear()
    except Exception:
        pass
    # Audit
    try:
        from services import audit
        audit.write(
            project_id,
            kind="analysis_delete",
            title=f"Analiz silindi: {meta.get('label') or analysis_id}",
            title_key="audit.titles.analysisDeleted",
            title_params={"label": meta.get("label") or analysis_id},
            details={
                "analysis_id": analysis_id,
                "method": meta.get("method"),
            },
            user_action="analysis_delete",
        )
    except Exception:
        pass
    active_id = analyses.get_active_analysis_id(project_id)
    # Bu analizin geçmiş kayıtlarını sil — her analizin AYRI geçmişi (Burak: "hepsinin
    # geçmişi ayrı olmalı"). Proje-seviye kurulum (upload vb.) ve DİĞER analizlerin
    # kayıtları korunur.
    try:
        from services import audit as _audit
        _audit.delete_for_analysis(project_id, analysis_id)
    except Exception:
        pass
    return {"ok": True, "active_id": active_id}
