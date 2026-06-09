"""Append-only audit log — kullanıcının her önemli işlemini kaydeder.

Her event JSON Lines (`operations.jsonl`) dosyasına yazılır:
    {ts, kind, title, details, user_action, snapshot, before, after}

Kind örnekleri:
    "upload"          — dosya yüklendi
    "convert"         — CSV/TXT → XLSX
    "merge"           — simple / enhanced
    "filter_save"     — preset kaydedildi
    "records_delete"  — toplu silme
    "enrich_api"      — API enrichment çalıştı
    "enrich_ml"       — ML enrichment çalıştı
    "disambiguate"    — yazar/affiliation cluster uygulandı
    "snapshot"        — snapshot alındı
    "export"          — dosya export edildi
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

from services import storage


_LOCK = threading.Lock()

# analysis_id sentinel'leri — her analizin AYRI geçmişi için.
_AUTO = "__auto__"  # write: kaydı aktif analize otomatik etiketle
_ALL = "__all__"    # read: filtre yok (tüm proje)


def _log_path(project_id: str) -> Path:
    return storage.project_dir(project_id) / "operations.jsonl"


def write(
    project_id: str,
    kind: str,
    title: str,
    *,
    title_key: Optional[str] = None,
    title_params: Optional[dict[str, Any]] = None,
    details: Optional[dict[str, Any]] = None,
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
    snapshot: Optional[str] = None,
    user_action: Optional[str] = None,
    analysis_id: Optional[str] = _AUTO,
) -> dict[str, Any]:
    """Yeni bir kayıt ekle. Hatalar sessizce yutulur — log bozulması ana akışı durdurmasın.

    `title`  — geriye dönük / markdown rapor için okunabilir metin (fallback).
    `title_key` + `title_params` — UI display-time i18n için (frontend t(key, params)).
    `analysis_id` — kaydın ait olduğu analiz (her analizin AYRI geçmişi). Varsayılan
        ("__auto__") aktif analizi otomatik bulur; proje-seviye işlemler (upload/file
        delete vb.) `analysis_id=None` geçer → tüm analizlerde "kurulum" olarak görünür.
    """
    if analysis_id == _AUTO:
        try:
            from services import analyses
            analysis_id = analyses.get_active_analysis_id(project_id)
        except Exception:
            analysis_id = None
    entry = {
        "ts": time.time(),
        "kind": kind,
        "title": title,
        "title_key": title_key,
        "title_params": title_params or None,
        "analysis_id": analysis_id,
        "details": details or {},
        "before": before,
        "after": after,
        "snapshot": snapshot,
        "user_action": user_action,
    }
    try:
        with _LOCK:
            p = _log_path(project_id)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass
    return entry


def read(
    project_id: str,
    limit: Optional[int] = None,
    analysis_id: Optional[str] = _ALL,
) -> list[dict[str, Any]]:
    """Audit kayıtlarını oku.

    `analysis_id`:
        "__all__"   → filtre yok (tüm proje geçmişi).
        <id> / None → o analizin kayıtları + proje-seviye (analysis_id is None) kurulum
                      kayıtları (upload vb.) — "o analizde nelerin yapıldığı".
    """
    p = _log_path(project_id)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    if analysis_id != _ALL:
        out = [e for e in out if e.get("analysis_id") == analysis_id or e.get("analysis_id") is None]
    if limit and limit > 0:
        out = out[-limit:]
    return out


def delete_for_analysis(project_id: str, analysis_id: str) -> int:
    """Bir analizin kayıtlarını sil (o analiz silinince çağrılır). Proje-seviye
    (analysis_id is None) kurulum kayıtları ve diğer analizlerin kayıtları KORUNUR."""
    p = _log_path(project_id)
    if not p.exists():
        return 0
    entries = read(project_id)  # tümü
    kept = [e for e in entries if e.get("analysis_id") != analysis_id]
    removed = len(entries) - len(kept)
    if removed <= 0:
        return 0
    try:
        with _LOCK:
            with p.open("w", encoding="utf-8") as f:
                for e in kept:
                    f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
    except Exception:
        return 0
    return removed


def clear(project_id: str) -> int:
    """Tüm log'u sil. Geri dönüşü olmayan operasyon (kullanıcı onayıyla)."""
    p = _log_path(project_id)
    if not p.exists():
        return 0
    count = sum(1 for _ in p.open("r", encoding="utf-8"))
    p.unlink()
    return count


def summary(project_id: str) -> dict[str, Any]:
    """Kaba özet — kategori bazlı sayım, ilk/son zaman."""
    entries = read(project_id)
    if not entries:
        return {"total": 0, "by_kind": {}, "first_ts": None, "last_ts": None}
    by_kind: dict[str, int] = {}
    for e in entries:
        by_kind[e.get("kind", "?")] = by_kind.get(e.get("kind", "?"), 0) + 1
    return {
        "total": len(entries),
        "by_kind": by_kind,
        "first_ts": entries[0].get("ts"),
        "last_ts": entries[-1].get("ts"),
    }


# ----- Markdown raporu -----

KIND_LABELS = {
    "project_create": "📋 Proje oluşturma",
    "upload": "📥 Dosya yükleme",
    "convert": "🔄 Dönüşüm",
    "merge": "🧩 Birleştirme",
    "merge_borderline": "🔍 Borderline kararı",
    "analysis_activate": "🎯 Aktif analiz değiştirildi",
    "analysis_delete": "🗑️ Analiz silindi",
    "filter_save": "⭐ Preset kaydı",
    "records_delete": "🗑️ Kayıt silme",
    "records_edit": "✏️ Kayıt düzenleme",
    "enrich_api": "🌐 API zenginleştirme",
    "enrich_ml": "🧠 ML zenginleştirme",
    "enrich_field": "🔧 Alan zenginleştirme",
    "enrich_selected": "🔧 Seçimsel zenginleştirme",
    "disambiguate": "✨ Disambiguation",
    "disambiguate_authors": "✨ Yazar Disambiguation",
    "disambiguate_affiliations": "🏢 Affiliation Disambiguation",
    "snapshot": "📸 Snapshot",
    "snapshot_restore": "↩️ Snapshot geri yükleme",
    "export": "📤 Export",
    "report": "📄 Rapor",
}


def _render_smart_merge_md(details: dict) -> list[str]:
    """Smart Merge audit entry için zengin Markdown bloğu."""
    lines: list[str] = []
    wos = details.get("wos_input", 0)
    scp = details.get("scopus_input", 0)
    merged = details.get("merged_count", 0)
    matched = details.get("matched_pairs", 0)
    borderline = details.get("borderline_count", 0)
    conflict = details.get("conflict_count", 0)
    lost_wos = details.get("lost_wos_count", 0)
    lost_scp = details.get("lost_scopus_count", 0)
    duration = details.get("duration_seconds")

    # Akış özeti
    lines.append("**Birleştirme Özeti:**")
    lines.append("")
    lines.append("| Metrik | Değer |")
    lines.append("|---|---|")
    lines.append(f"| Ham girdi | {wos} WoS + {scp} Scopus = **{wos + scp}** |")
    lines.append(f"| Eşleştirilen çift | **{matched}** |")
    lines.append(f"| Borderline (manuel onay bekleyen) | {borderline} |")
    lines.append(f"| Eşleşmemiş WoS (lost) | {lost_wos} |")
    lines.append(f"| Eşleşmemiş Scopus (lost) | {lost_scp} |")
    lines.append(f"| Çıktı (benzersiz kayıt) | **{merged}** |")
    dedup_rate = (wos + scp - merged) / (wos + scp) if (wos + scp) else 0
    lines.append(f"| Tekilleştirme oranı | %{dedup_rate * 100:.1f} |")
    if duration:
        lines.append(f"| Süre | {duration:.1f} sn |")
    lines.append("")

    # Eşleşme aşamaları
    stages = details.get("match_stages") or {}
    if stages:
        lines.append("**Eşleşme Aşamaları** _(Hammerton 2013 + Caputo 2024)_:")
        lines.append("")
        lines.append("| Aşama | Eşleşme |")
        lines.append("|---|---|")
        for stage, count in sorted(stages.items(), key=lambda x: -x[1]):
            lines.append(f"| {stage} | {count} |")
        lines.append("")

    # Çakışma kaynak dağılımı (Caputo 2024)
    field_dist = details.get("field_source_distribution") or {}
    if field_dist:
        total = sum(field_dist.values())
        lines.append(f"**Alan Çakışma Çözümü** _({conflict} çakışma — Caputo 2024 sabit defaults)_:")
        lines.append("")
        lines.append("| Tercih Edilen Kaynak | Sayı | Yüzde |")
        lines.append("|---|---|---|")
        for src, count in sorted(field_dist.items(), key=lambda x: -x[1]):
            pct = (count / total * 100) if total else 0
            lines.append(f"| {src} | {count} | %{pct:.1f} |")
        lines.append("")

    # Üretilen dosyalar
    files = details.get("output_files") or []
    if files:
        lines.append("**Üretilen Dosyalar:**")
        for f in files:
            lines.append(f"- `{f}`")
        lines.append("")

    return lines


def format_markdown_report(project_id: str, analysis_id: Optional[str] = _ALL) -> str:
    import datetime as dt
    meta = storage.get_project(project_id)
    entries = read(project_id, analysis_id=analysis_id)
    name = meta.name if meta else project_id

    lines: list[str] = []
    lines.append(f"# BibexPy v2 — Operasyon Raporu")
    lines.append("")
    lines.append(f"**Proje:** {name} (`{project_id}`)  ")
    lines.append(f"**Oluşturma:** {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}  ")
    lines.append(f"**Toplam operasyon:** {len(entries)}")
    lines.append("")

    if not entries:
        lines.append("_Henüz operasyon kaydı yok._")
        return "\n".join(lines)

    # Özet — filtrelenmiş kayıtlardan (aktif analiz geçmişi)
    by_kind: dict[str, int] = {}
    for e in entries:
        by_kind[e.get("kind", "?")] = by_kind.get(e.get("kind", "?"), 0) + 1
    lines.append("## Özet")
    lines.append("")
    lines.append("| Kategori | Sayı |")
    lines.append("|---|---|")
    for k, n in sorted(by_kind.items(), key=lambda x: -x[1]):
        lines.append(f"| {KIND_LABELS.get(k, k)} | {n} |")
    lines.append("")

    lines.append("## Kronoloji")
    lines.append("")
    for i, e in enumerate(entries, 1):
        ts = e.get("ts")
        ts_str = dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "—"
        label = KIND_LABELS.get(e.get("kind", ""), e.get("kind", "?"))
        title = e.get("title", "")
        lines.append(f"### {i}. {label} — {title}")
        lines.append(f"_{ts_str}_")
        details = e.get("details") or {}

        # Smart Merge için zengin format — JSON dump değil
        if e.get("kind") == "merge" and details.get("method") == "smart":
            lines.append("")
            lines.extend(_render_smart_merge_md(details))
            if e.get("snapshot"):
                lines.append(f"- 📸 snapshot: `{e['snapshot']}`")
            lines.append("")
            continue

        if details:
            lines.append("")
            for k, v in details.items():
                if v is None or v == "":
                    continue
                if isinstance(v, (list, dict)):
                    v = json.dumps(v, ensure_ascii=False)
                if len(str(v)) > 200:
                    v = str(v)[:200] + "…"
                lines.append(f"- **{k}:** {v}")
        before = e.get("before")
        after = e.get("after")
        if before or after:
            lines.append("")
            if before:
                lines.append(f"- _önce:_ `{json.dumps(before, ensure_ascii=False)}`")
            if after:
                lines.append(f"- _sonra:_ `{json.dumps(after, ensure_ascii=False)}`")
        if e.get("snapshot"):
            lines.append(f"- 📸 snapshot: `{e['snapshot']}`")
        if e.get("user_action"):
            lines.append(f"- 👤 kullanıcı: _{e['user_action']}_")
        lines.append("")
    return "\n".join(lines)
