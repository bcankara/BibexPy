"""Veri kalitesi & grafik endpoint'leri.

UI bu endpoint'leri:
- Dashboard panelinde alan-bazlı doluluk yüzdelerini göstermek için
- Records sayfası üstündeki chart'lar için kullanır.
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
import pandas as pd

from config import settings
from services import analyses, audit, filter_engine, storage


router = APIRouter(prefix="/projects/{project_id}/quality", tags=["quality"])


# Kalite panelinde izlenen alanlar — VOSviewer & Biblioshiny analizlerinin
# kullandığı sütunlara hizalı (kritiklik hint metninde). Bir alan yalnız sütunu
# merged dataset'te varsa gösterilir (available=True). Label = i18n fallback'i;
# UI gerçek etiketi recordDetail.fields.{kod}'tan alır.
QUALITY_FIELDS = [
    # ── Tier 1 — kritik (eş-yazarlık / atıf ağları + Biblioshiny çekirdek) ──
    ("AU", "Authors", "Critical: author & co-authorship analysis unit"),
    ("PY", "Year", "Critical: temporal analysis / production over time"),
    ("SO", "Source/Journal", "Critical: source coupling, Bradford, journal analysis"),
    ("TC", "Times Cited", "Critical: citation / impact analysis"),
    ("CR", "Cited References", "Critical: co-citation & bibliographic coupling"),
    # ── Tier 2 — önemli ──
    ("TI", "Title", "Important: term map, record identity"),
    ("DE", "Author Keywords", "Important: co-word / keyword co-occurrence"),
    ("ID", "Keywords Plus", "Important: co-word (WoS-generated)"),
    ("C1", "Addresses", "Important: country & institution collaboration"),
    # ── Tier 3 — faydalı ──
    ("AB", "Abstract", "Useful: term map, ML & disambiguation input"),
    ("AF", "Author Affiliations", "Useful: author-institution pairs"),
    ("WC", "WoS Categories", "Useful: thematic categorization"),
    ("SC", "Subject Categories", "Useful: higher-level categorization"),
    ("DI", "DOI", "Useful: citability, enrichment key"),
    ("LA", "Language", "Useful: language filtering"),
]

# Bibliometrik AĞIRLIK — health_score, alanları analitik önemine göre ağırlıklandırır
# (alttaki bar "ne kadarı boş"u; health "veri bibliometrik analiz için ne kadar değerli"yi
# gösterir). Ağırlıklar VOSviewer/Biblioshiny kritikliğinden (Tier 1/2/3) türetilmiştir:
#   Tier 1 (kritik) = 3 : olmazsa ana ağ analizleri (co-authorship, co-citation, kaynak,
#                          atıf, zaman) kurulamaz.
#   Tier 2 (önemli) = 2 : co-word, ülke/kurum işbirliği, başlık-terim haritaları.
#   Tier 3 (faydalı)= 1 : tamamlayıcı / filtreleme / kimlik alanları.
FIELD_WEIGHTS = {
    "AU": 3, "PY": 3, "SO": 3, "TC": 3, "CR": 3,        # Tier 1
    "TI": 2, "DE": 2, "ID": 2, "C1": 2,                 # Tier 2
    "AB": 1, "AF": 1, "WC": 1, "SC": 1, "DI": 1, "LA": 1,  # Tier 3
}


def _is_filled(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    return (s != "") & (s.str.upper() != "NAN") & s.notna()


def _compute_stats(df: pd.DataFrame) -> dict:
    """Alan-bazlı doluluk + ağırlıklı health skoru + DB dağılımı (stats ve overview ortak)."""
    total = int(len(df))
    fields = []
    for code, label, hint in QUALITY_FIELDS:
        if code not in df.columns:
            fields.append({
                "field": code, "label": label, "hint": hint,
                "total": total, "filled": 0, "missing": total,
                "fill_rate": 0.0, "available": False,
            })
            continue
        filled_mask = _is_filled(df[code])
        filled = int(filled_mask.sum())
        fields.append({
            "field": code, "label": label, "hint": hint,
            "total": total, "filled": filled, "missing": total - filled,
            "fill_rate": (filled / total) if total else 0.0,
            "available": True,
        })

    num = sum(f["fill_rate"] * FIELD_WEIGHTS.get(f["field"], 1) for f in fields if f["available"])
    den = sum(FIELD_WEIGHTS.get(f["field"], 1) for f in fields if f["available"])
    health = float(num / den) if den else 0.0

    db_dist = {}
    if "DB" in df.columns:
        db_dist = df["DB"].astype(str).str.strip().value_counts().head(10).to_dict()
        db_dist = {str(k): int(v) for k, v in db_dist.items()}

    return {
        "total_records": total,
        "health_score": health,
        "fields": fields,
        "db_distribution": db_dist,
    }


@router.get("/last-fill-report")
def get_last_fill_report(project_id: str):
    """En son 'Fill all' (fill_all) işleminin özetini döndür — sayfa yenilense bile
    rapor ekranda kalsın diye. Hiç çalıştırılmamışsa {"report": None}.

    Audit'te fill_all kaydı kind='enrich_api' + details.method=='fill_all' ile tutulur.
    """
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    # Rapor YALNIZ mevcut aktif analize ait olmalı — yeni bir merge (yeni analiz)
    # eski enrichment raporunu taşımasın. Aktif analizden farklı (veya analysis_id
    # taşımayan eski) kayıtlar gösterilmez.
    active_id = analyses.get_active_analysis_id(project_id)
    for entry in reversed(audit.read(project_id)):
        d = entry.get("details") or {}
        if entry.get("kind") == "enrich_api" and d.get("method") == "fill_all":
            if active_id is not None and d.get("analysis_id") != active_id:
                continue  # başka (eski) analizin raporu — atla
            return {
                "report": {
                    "ts": entry.get("ts"),
                    "enriched": d.get("enriched", 0),
                    "api": d.get("api"),
                    "doi": d.get("doi"),
                    "fill_rate_before": d.get("fill_rate_before"),
                    "fill_rate_after": d.get("fill_rate_after"),
                    "per_field_fill": d.get("per_field_fill"),
                    "snapshot": d.get("snapshot") or entry.get("snapshot"),
                }
            }
    return {"report": None}


@router.get("/stats")
def get_quality_stats(project_id: str):
    """Her alan için doluluk %, eksik sayısı, top değerler.

    health_score = bibliometrik AĞIRLIKLI doluluk ortalaması (kritik alanlar düşükse
    skor sert düşer; tamamlayıcı alanlar eksikse az etkilenir). Bkz. _compute_stats.
    """
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    try:
        df = filter_engine.load_merged(project_id)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))
    return _compute_stats(df)


def _overview_cleanup(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


@router.get("/overview")
def download_overview(project_id: str, background_tasks: BackgroundTasks, fmt: str = "csv"):
    """Genel Bakış tablosunu indir (Biblioshiny-tarzı özet) — CSV veya XLSX.

    Rapora yapıştırılabilir tablo: alan kodu · etiket · toplam · dolu · eksik · doluluk%.
    XLSX'te ayrıca 'Summary' sayfası (toplam kayıt, health skoru, DB dağılımı) yer alır.
    PNG çıktısı istemci tarafında (html-to-image) üretilir; backend yalnız tabloyu verir.
    """
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    if fmt not in ("csv", "xlsx"):
        raise HTTPException(400, "invalid_format")
    try:
        df = filter_engine.load_merged(project_id)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))

    stats = _compute_stats(df)
    fields_table = pd.DataFrame([
        {
            "Field": f["field"],
            "Label": f["label"],
            "Total": f["total"],
            "Filled": f["filled"],
            "Missing": f["missing"],
            "Fill rate (%)": round(f["fill_rate"] * 100, 1),
        }
        for f in stats["fields"] if f["available"]
    ])

    stamp = time.strftime("%Y%m%d_%H%M%S")
    workdir = settings.storage_path / "tools_tmp" / uuid.uuid4().hex[:12]
    workdir.mkdir(parents=True, exist_ok=True)
    background_tasks.add_task(_overview_cleanup, workdir)
    out_path = workdir / f"data_health_{stamp}.{fmt}"

    if fmt == "csv":
        # utf-8-sig → Excel Türkçe karakterleri doğru açsın.
        fields_table.to_csv(out_path, index=False, encoding="utf-8-sig")
    else:
        summary_rows = [
            {"Metric": "Total records", "Value": stats["total_records"]},
            {"Metric": "Health score (%)", "Value": round(stats["health_score"] * 100, 1)},
        ]
        for db, n in (stats.get("db_distribution") or {}).items():
            summary_rows.append({"Metric": f"Database · {db}", "Value": n})
        with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
            pd.DataFrame(summary_rows).to_excel(xw, sheet_name="Summary", index=False)
            fields_table.to_excel(xw, sheet_name="Fields", index=False)

    media = "text/csv" if fmt == "csv" else \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return FileResponse(path=str(out_path), filename=out_path.name, media_type=media)


@router.get("/charts")
def get_charts(project_id: str):
    """Görseller için chart verileri."""
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    try:
        df = filter_engine.load_merged(project_id)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))

    out: dict = {}

    # Yıl histogramı + doc_type ile breakdown
    if "PY" in df.columns:
        years = pd.to_numeric(df["PY"], errors="coerce").dropna().astype(int)
        if len(years):
            year_counts = years.value_counts().sort_index()
            out["year_histogram"] = [{"year": int(y), "count": int(c)} for y, c in year_counts.items()]

            # Yıl × doc type cross-tab (stacked bar için)
            if "DT" in df.columns:
                df2 = df.copy()
                df2["_y"] = pd.to_numeric(df2["PY"], errors="coerce")
                df2 = df2.dropna(subset=["_y"])
                df2["_y"] = df2["_y"].astype(int)
                # En sık 5 tipi al
                top_types = df2["DT"].astype(str).str.upper().value_counts().head(5).index.tolist()
                ct = pd.crosstab(df2["_y"], df2["DT"].astype(str).str.upper())
                ct = ct[[c for c in top_types if c in ct.columns]]
                out["year_by_doctype"] = {
                    "types": top_types,
                    "data": [
                        {"year": int(y), **{t: int(ct.loc[y].get(t, 0)) for t in top_types}}
                        for y in ct.index
                    ],
                }

    # Top 15 dergi (SO)
    if "SO" in df.columns:
        so_counts = df["SO"].astype(str).str.strip().replace("", pd.NA).dropna().value_counts().head(15)
        out["top_journals"] = [{"name": str(k), "count": int(v)} for k, v in so_counts.items()]

    # Doc type dağılımı (pie)
    if "DT" in df.columns:
        dt_counts = df["DT"].astype(str).str.strip().str.upper().replace("", pd.NA).dropna().value_counts().head(10)
        out["doc_types"] = [{"name": str(k), "count": int(v)} for k, v in dt_counts.items()]

    # Dil dağılımı
    if "LA" in df.columns:
        la_counts = df["LA"].astype(str).str.strip().str.upper().replace("", pd.NA).dropna().value_counts().head(10)
        out["languages"] = [{"name": str(k), "count": int(v)} for k, v in la_counts.items()]

    # Atıf dağılımı (histogram bucketları)
    if "TC" in df.columns:
        tc = pd.to_numeric(df["TC"], errors="coerce").dropna().astype(int)
        if len(tc):
            # 0, 1-5, 6-10, 11-25, 26-50, 51-100, 100+
            buckets = [(0, 0), (1, 5), (6, 10), (11, 25), (26, 50), (51, 100), (101, 10**9)]
            labels = ["0", "1-5", "6-10", "11-25", "26-50", "51-100", "100+"]
            data = []
            for (lo, hi), lab in zip(buckets, labels):
                if lo == 0 and hi == 0:
                    n = int((tc == 0).sum())
                else:
                    n = int(((tc >= lo) & (tc <= hi)).sum())
                data.append({"bucket": lab, "count": n})
            out["citation_buckets"] = data

    return out
