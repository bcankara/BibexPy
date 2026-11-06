"""Language dictionaries and formatting helpers for downloadable documents.

The operation-log documents (Markdown, TXT, PDF) and the data-flow PDF follow
the UI language (``en`` | ``tr``) chosen by the caller. The backend's canonical
text stays English everywhere else; this is the single module that carries
Turkish strings. The wording mirrors the frontend i18n files
(``audit.kindLabels.*``, ``audit.kinds.*``, ``audit.smart.*``,
``audit.titles.*`` and the filter panel names), and a parity test keeps the
audit title keys and their ``{placeholders}`` in sync with those files.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Optional

LANGS = ("en", "tr")


def norm_lang(lang: Optional[str]) -> str:
    """Coerce any value to ``en`` | ``tr`` (anything not starting with ``tr`` is ``en``)."""
    return "tr" if str(lang or "").strip().lower().startswith("tr") else "en"


# ─────────────────────────────────────────────────────────────────────────
#  Dictionaries (flat dotted keys)
# ─────────────────────────────────────────────────────────────────────────

_EN: dict[str, str] = {
    # Document headings
    "doc.title": "Operation Report",
    "doc.pdfTitle": "Operation Report — {name}",
    "doc.project": "Project",
    "doc.generated": "Generated",
    "doc.totalOps": "Total operations",
    "doc.summary": "Summary",
    "doc.category": "Category",
    "doc.count": "Count",
    "doc.chronology": "Chronology",
    "doc.noOps": "No operations recorded yet.",
    "doc.snapshot": "snapshot",
    "doc.user": "user",
    "doc.before": "before",
    "doc.after": "after",
    "doc.fallbackTitle": "Report",
    # Smart Merge block
    "smart.summaryHeading": "Merge summary:",
    "smart.metric": "Metric",
    "smart.value": "Value",
    "smart.rawInput": "Raw input",
    "smart.rawInputValue": "{wos} WoS + {scopus} Scopus = **{total}**",
    "smart.matchedPairs": "Matched pairs",
    "smart.borderlinePending": "Borderline (awaiting manual review)",
    "smart.lostWos": "Unmatched WoS (lost)",
    "smart.lostScopus": "Unmatched Scopus (lost)",
    "smart.output": "Output (unique records)",
    "smart.dedupRate": "Deduplication rate",
    "smart.duration": "Duration",
    "smart.seconds": "sec",
    "smart.stagesHeading": "Match stages",
    "smart.stage": "Stage",
    "smart.matches": "Matches",
    "smart.conflictHeading": "Field conflict resolution",
    "smart.conflictNote": "{conflict} conflicts — Caputo 2024 fixed defaults",
    "smart.preferredSource": "Preferred source",
    "smart.percent": "Percent",
    "smart.filesHeading": "Files produced:",
    # Match stages (keys normalised by _stage_key)
    "stage.doi_exact": "DOI exact",
    "stage.pmid_exact": "PMID exact",
    "stage.title_year_surname": "Title + year + surname",
    "stage.journal_vol_pages": "Journal + volume + pages",
    "stage.borderline_manual_review": "Borderline (manual review)",
    # Field-source codes
    "src.wos": "WoS",
    "src.scopus": "Scopus",
    "src.union": "Union",
    "src.empty": "Empty",
    "src.scopus_fallback": "Scopus (fallback)",
    "src.wos_fallback": "WoS (fallback)",
    # Job status words
    "jobstatus.queued": "queued",
    "jobstatus.running": "running",
    "jobstatus.completed": "completed",
    "jobstatus.failed": "failed",
    "jobstatus.cancelled": "cancelled",
    # Detail keys
    "detail.method": "method",
    "detail.name": "name",
    "detail.removed": "removed",
    "detail.kept": "kept",
    "detail.deleted": "deleted",
    "detail.total": "total",
    "detail.model": "model",
    "detail.filter_keys": "filter criteria",
    "detail.applied_changes": "applied changes",
    "detail.duration_seconds": "duration (sec)",
    "detail.wos_input": "WoS input",
    "detail.scopus_input": "Scopus input",
    "detail.merged_count": "merged records",
    "detail.matched_pairs": "matched pairs",
    "detail.borderline_count": "borderline pairs",
    "detail.status": "status",
    # Filter criteria names (mirror the filter panel)
    "filter.year": "Year",
    "filter.doc_type": "Document type",
    "filter.language": "Language",
    "filter.db_source": "Database",
    "filter.journal": "Journal",
    "filter.authors": "Author",
    "filter.wc_categories": "WoS Categories",
    "filter.sc_categories": "Subject Categories",
    "filter.citation_count": "Citation count",
    "filter.fulltext": "Full-text search",
    "filter.quality": "Quality / Missing data",
    # Operation categories (audit.kindLabels.* — emoji kept for Markdown)
    "kind.project_create": "📋 Project created",
    "kind.upload": "📥 File upload",
    "kind.convert": "🔄 Conversion",
    "kind.merge": "🧩 Merge",
    "kind.merge_borderline": "🔍 Borderline decision",
    "kind.analysis_activate": "🎯 Active analysis changed",
    "kind.analysis_delete": "🗑️ Analysis deleted",
    "kind.filter_save": "⭐ Preset saved",
    "kind.filter_apply": "🔽 Filter applied",
    "kind.records_delete": "🗑️ Records deleted",
    "kind.records_edit": "✏️ Record edit",
    "kind.enrich_api": "🌐 API enrichment",
    "kind.enrich_ml": "🧠 ML enrichment",
    "kind.enrich_field": "🔧 Field enrichment",
    "kind.enrich_selected": "🔧 Selected enrichment",
    "kind.disambiguate": "✨ Disambiguation",
    "kind.disambiguate_authors": "✨ Author disambiguation",
    "kind.disambiguate_affiliations": "🏢 Affiliation disambiguation",
    "kind.countries": "🌍 Country standardization",
    "kind.organizations": "🏢 Affiliation roll-up",
    "kind.snapshot": "📸 Snapshot",
    "kind.snapshot_restore": "↩️ Snapshot restore",
    "kind.format_migration": "📦 Format migration",
    "kind.export": "📤 Export",
    "kind.report": "📄 Report",
    # User action codes
    "action.analysis_activate": "Analysis activated",
    "action.analysis_delete": "Analysis deleted",
    "action.apply_disambiguation": "Disambiguation applied",
    "action.auto": "Automatic",
    "action.auto_prepare": "Automatic preparation",
    "action.borderline_decide": "Borderline decision",
    "action.convert": "Conversion",
    "action.copy_to_folder": "Copied to folder",
    "action.create_project": "Project created",
    "action.delete_file": "File deleted",
    "action.delete_preset": "Preset deleted",
    "action.delete_processed_file": "Processed file deleted",
    "action.export": "Export",
    "action.export_delete": "Export deleted",
    "action.filter_apply": "Filter applied",
    "action.generate_methodology": "Methodology generated",
    "action.manual_bulk_delete": "Bulk delete",
    "action.manual_edit": "Manual edit",
    "action.prepare_reset": "Preparation reset",
    "action.restore": "Snapshot restored",
    "action.save_preset": "Preset saved",
    "action.upload": "Upload",
    "action.job_completed": "Job completed",
    # Data-flow PDF (mirror report.flow.*)
    "flow.pdfTitle": "Data Flow Diagram",
    "flow.identification": "Identification (raw input)",
    "flow.sources": "WoS {wos} + Scopus {scopus}",
    "flow.records": "{n} records",
    "flow.dedup": "Deduplication — Smart Merge",
    "flow.pairsMerged": "{n} pairs merged",
    "flow.intraRemoved": "intra-source duplicates: {n}",
    "flow.removed": "−{n} records removed",
    "flow.unique": "Unique record set",
    "flow.borderlineKept": "{n} uncertain pairs kept separate",
    "flow.stepFilter": "Filter applied: {keys}",
    "flow.stepDelete": "Manual record deletion",
    "flow.stepBorderline": "Uncertain pairs accepted (merged)",
    "flow.final": "Final dataset",
    # Audit titles (copied from apps/web/lib/i18n/en.json: audit.titles.*)
    "audit.titles.disambiguationApplied": "Disambiguation applied ({kind}) — {n} changes",
    "audit.titles.filterApplied": "Filter applied to dataset: {kept} kept / {removed} removed",
    "audit.titles.disambiguationRestored": "Disambiguation snapshot restored",
    "audit.titles.filesCopiedToFolder": "{n} files copied to local folder",
    "audit.titles.merge": "Merge ({method})",
    "audit.titles.autoPrepare": "Auto-prepare — {csv} CSV + {txt} TXT",
    "audit.titles.prepareReset": "Preparation reset — {n} processed files deleted (raw files kept)",
    "audit.titles.processedFileDeleted": "Processed file deleted: {name}",
    "audit.titles.recordsDeleted": "{n} records deleted",
    "audit.titles.snapshotRestored": "Snapshot restored: {name}",
    "audit.titles.parquetMigrated": "Dataset migrated to Parquet format (one-time speedup)",
    "audit.titles.fieldEnrichment": "Field enrichment: {field} ({label})",
    "audit.titles.selectedEnrichment": "Enrichment for {n} selected records",
    "audit.titles.filesUploaded": "{n} files uploaded",
    "audit.titles.authorDisambiguation": "Author disambiguation",
    "audit.titles.affiliationStandardization": "Affiliation standardization",
    "audit.titles.projectCreated": "Project created: {name}",
    "audit.titles.apiEnrichment": "API enrichment",
    "audit.titles.mlEnrichment": "ML enrichment",
    "audit.titles.methodologyGenerated": "Methodology report generated (LLM)",
    "audit.titles.fillAll": "Fill missing fields (API)",
    "audit.titles.countryStandardization": "Country standardization",
    "audit.titles.orgRollup": "Affiliation roll-up (parent organization)",
    "audit.titles.recordEdited": "Record edited ({n} fields)",
    "audit.titles.analysisActivated": "Active analysis: {label}",
    "audit.titles.analysisDeleted": "Analysis deleted: {label}",
    "audit.titles.presetSaved": "Preset saved: {name}",
    "audit.titles.presetDeleted": "Preset deleted: {name}",
    "audit.titles.exported": "Export ({fmt}): {name}",
    "audit.titles.exportDeleted": "Export deleted: {name}",
    "audit.titles.converted": "Converted: {name}",
    "audit.titles.fileDeleted": "File deleted: {name}",
    "audit.titles.borderlineDecisions": "Borderline pairs: {accept} accepted / {reject} rejected / {skip} skipped",
    "src.cross_fill": "Cross-filled",
    "action.job_failed": "Job failed",
    "action.job_cancelled": "Job cancelled",
    "action.copy_browser": "Copied via browser",
    "audit.titles.uidMigration": "Records updated for the new version: {records} record(s) in {analysis} now use sequential IDs",
}

_TR: dict[str, str] = {
    "doc.title": "Operasyon Raporu",
    "doc.pdfTitle": "Operasyon Raporu — {name}",
    "doc.project": "Proje",
    "doc.generated": "Oluşturma",
    "doc.totalOps": "Toplam operasyon",
    "doc.summary": "Özet",
    "doc.category": "Kategori",
    "doc.count": "Sayı",
    "doc.chronology": "Kronoloji",
    "doc.noOps": "Henüz operasyon kaydı yok.",
    "doc.snapshot": "snapshot",
    "doc.user": "kullanıcı",
    "doc.before": "önce",
    "doc.after": "sonra",
    "doc.fallbackTitle": "Rapor",
    "smart.summaryHeading": "Birleştirme Özeti:",
    "smart.metric": "Metrik",
    "smart.value": "Değer",
    "smart.rawInput": "Ham girdi",
    "smart.rawInputValue": "{wos} WoS + {scopus} Scopus = **{total}**",
    "smart.matchedPairs": "Eşleştirilen çift",
    "smart.borderlinePending": "Borderline (manuel onay bekleyen)",
    "smart.lostWos": "Eşleşmemiş WoS (kayıp)",
    "smart.lostScopus": "Eşleşmemiş Scopus (kayıp)",
    "smart.output": "Çıktı (benzersiz kayıt)",
    "smart.dedupRate": "Tekilleştirme oranı",
    "smart.duration": "Süre",
    "smart.seconds": "sn",
    "smart.stagesHeading": "Eşleşme Aşamaları",
    "smart.stage": "Aşama",
    "smart.matches": "Eşleşme",
    "smart.conflictHeading": "Alan Çakışma Çözümü",
    "smart.conflictNote": "{conflict} çakışma — Caputo 2024 sabit varsayılanları",
    "smart.preferredSource": "Tercih Edilen Kaynak",
    "smart.percent": "Yüzde",
    "smart.filesHeading": "Üretilen Dosyalar:",
    "stage.doi_exact": "DOI tam eşleşme",
    "stage.pmid_exact": "PMID tam eşleşme",
    "stage.title_year_surname": "Başlık + yıl + soyad",
    "stage.journal_vol_pages": "Dergi + cilt + sayfa",
    "stage.borderline_manual_review": "Belirsiz (elle inceleme)",
    "src.wos": "WoS",
    "src.scopus": "Scopus",
    "src.union": "Birleşim",
    "src.empty": "Boş",
    "src.scopus_fallback": "Scopus (yedek)",
    "src.wos_fallback": "WoS (yedek)",
    "jobstatus.queued": "sırada",
    "jobstatus.running": "çalışıyor",
    "jobstatus.completed": "tamamlandı",
    "jobstatus.failed": "başarısız",
    "jobstatus.cancelled": "iptal edildi",
    "detail.method": "yöntem",
    "detail.name": "ad",
    "detail.removed": "çıkarılan",
    "detail.kept": "kalan",
    "detail.deleted": "silinen",
    "detail.total": "toplam",
    "detail.model": "model",
    "detail.filter_keys": "filtre ölçütleri",
    "detail.applied_changes": "uygulanan değişiklik",
    "detail.duration_seconds": "süre (sn)",
    "detail.wos_input": "WoS girdisi",
    "detail.scopus_input": "Scopus girdisi",
    "detail.merged_count": "birleşen kayıt",
    "detail.matched_pairs": "eşleşen çift",
    "detail.borderline_count": "borderline çift",
    "detail.status": "durum",
    "filter.year": "Yıl",
    "filter.doc_type": "Doküman tipi",
    "filter.language": "Dil",
    "filter.db_source": "Veritabanı",
    "filter.journal": "Dergi",
    "filter.authors": "Yazar",
    "filter.wc_categories": "WoS Kategorileri",
    "filter.sc_categories": "Konu Kategorileri",
    "filter.citation_count": "Atıf sayısı",
    "filter.fulltext": "Tam metin arama",
    "filter.quality": "Kalite / Eksik veri",
    "kind.project_create": "📋 Proje oluşturma",
    "kind.upload": "📥 Dosya yükleme",
    "kind.convert": "🔄 Dönüşüm",
    "kind.merge": "🧩 Birleştirme",
    "kind.merge_borderline": "🔍 Borderline kararı",
    "kind.analysis_activate": "🎯 Aktif analiz değiştirildi",
    "kind.analysis_delete": "🗑️ Analiz silindi",
    "kind.filter_save": "⭐ Preset kaydı",
    "kind.filter_apply": "🔽 Filtre uygulama",
    "kind.records_delete": "🗑️ Kayıt silme",
    "kind.records_edit": "✏️ Kayıt düzenleme",
    "kind.enrich_api": "🌐 API zenginleştirme",
    "kind.enrich_ml": "🧠 ML zenginleştirme",
    "kind.enrich_field": "🔧 Alan zenginleştirme",
    "kind.enrich_selected": "🔧 Seçimsel zenginleştirme",
    "kind.disambiguate": "✨ Disambiguation",
    "kind.disambiguate_authors": "✨ Yazar disambiguation",
    "kind.disambiguate_affiliations": "🏢 Kurum ayrıştırma",
    "kind.countries": "🌍 Ülke standartlaştırma",
    "kind.organizations": "🏢 Kurum toplama",
    "kind.snapshot": "📸 Snapshot",
    "kind.snapshot_restore": "↩️ Snapshot geri yükleme",
    "kind.format_migration": "📦 Format taşıma",
    "kind.export": "📤 Dışa aktarma",
    "kind.report": "📄 Rapor",
    "action.analysis_activate": "Analiz etkinleştirildi",
    "action.analysis_delete": "Analiz silindi",
    "action.apply_disambiguation": "Disambiguation uygulandı",
    "action.auto": "Otomatik",
    "action.auto_prepare": "Otomatik hazırlık",
    "action.borderline_decide": "Belirsiz çift kararı",
    "action.convert": "Dönüşüm",
    "action.copy_to_folder": "Klasöre kopyalandı",
    "action.create_project": "Proje oluşturuldu",
    "action.delete_file": "Dosya silindi",
    "action.delete_preset": "Preset silindi",
    "action.delete_processed_file": "İşlenmiş dosya silindi",
    "action.export": "Dışa aktarma",
    "action.export_delete": "Dışa aktarma silindi",
    "action.filter_apply": "Filtre uygulandı",
    "action.generate_methodology": "Metodoloji üretildi",
    "action.manual_bulk_delete": "Toplu silme",
    "action.manual_edit": "Elle düzenleme",
    "action.prepare_reset": "Hazırlık sıfırlandı",
    "action.restore": "Snapshot geri yüklendi",
    "action.save_preset": "Preset kaydedildi",
    "action.upload": "Yükleme",
    "action.job_completed": "İş tamamlandı",
    "flow.pdfTitle": "Veri Akış Şeması",
    "flow.identification": "Tanımlama (ham girdi)",
    "flow.sources": "WoS {wos} + Scopus {scopus}",
    "flow.records": "{n} kayıt",
    "flow.dedup": "Tekilleştirme — Smart Merge",
    "flow.pairsMerged": "{n} çift birleştirildi",
    "flow.intraRemoved": "kaynak-içi kopya: {n}",
    "flow.removed": "−{n} kayıt çıkarıldı",
    "flow.unique": "Benzersiz kayıt kümesi",
    "flow.borderlineKept": "{n} belirsiz çift ayrı tutuldu",
    "flow.stepFilter": "Filtre uygulandı: {keys}",
    "flow.stepDelete": "Elle kayıt silme",
    "flow.stepBorderline": "Belirsiz çift onayı (birleştirildi)",
    "flow.final": "Nihai veri seti",
    # Audit titles (copied from apps/web/lib/i18n/tr.json: audit.titles.*)
    "audit.titles.disambiguationApplied": "Disambiguation uygulandı ({kind}) — {n} değişiklik",
    "audit.titles.filterApplied": "Filtre veri setine uygulandı: {kept} kayıt kaldı / {removed} çıkarıldı",
    "audit.titles.disambiguationRestored": "Disambiguation snapshot geri yüklendi",
    "audit.titles.filesCopiedToFolder": "{n} dosya lokal klasöre kopyalandı",
    "audit.titles.merge": "Birleştirme ({method})",
    "audit.titles.autoPrepare": "Otomatik hazırlık — {csv} CSV + {txt} TXT",
    "audit.titles.prepareReset": "Hazırlık sıfırlandı — {n} işlenmiş dosya silindi (ham dosyalar korundu)",
    "audit.titles.processedFileDeleted": "Hazırlanmış dosya silindi: {name}",
    "audit.titles.recordsDeleted": "{n} kayıt silindi",
    "audit.titles.snapshotRestored": "Snapshot geri yüklendi: {name}",
    "audit.titles.parquetMigrated": "Dataset Parquet formatına taşındı (tek seferlik hızlandırma)",
    "audit.titles.fieldEnrichment": "Alan zenginleştirme: {field} ({label})",
    "audit.titles.selectedEnrichment": "{n} seçili kayıt için zenginleştirme",
    "audit.titles.filesUploaded": "{n} dosya yüklendi",
    "audit.titles.authorDisambiguation": "Yazar Ayrıştırma",
    "audit.titles.affiliationStandardization": "Kurum Normalizasyonu",
    "audit.titles.projectCreated": "Proje oluşturuldu: {name}",
    "audit.titles.apiEnrichment": "API ile zenginleştirme",
    "audit.titles.mlEnrichment": "ML ile zenginleştirme",
    "audit.titles.methodologyGenerated": "Metodoloji raporu üretildi (LLM)",
    "audit.titles.fillAll": "Eksik alanları doldur (API)",
    "audit.titles.countryStandardization": "Ülke standartlaştırma",
    "audit.titles.orgRollup": "Kurum toplama (üst kurum)",
    "audit.titles.recordEdited": "Kayıt düzenlendi ({n} alan)",
    "audit.titles.analysisActivated": "Aktif analiz: {label}",
    "audit.titles.analysisDeleted": "Analiz silindi: {label}",
    "audit.titles.presetSaved": "Preset kaydedildi: {name}",
    "audit.titles.presetDeleted": "Preset silindi: {name}",
    "audit.titles.exported": "Dışa aktarma ({fmt}): {name}",
    "audit.titles.exportDeleted": "Dışa aktarma silindi: {name}",
    "audit.titles.converted": "Dönüştürüldü: {name}",
    "audit.titles.fileDeleted": "Dosya silindi: {name}",
    "audit.titles.borderlineDecisions": "Belirsiz çiftler: {accept} kabul / {reject} red / {skip} atlandı",
    "src.cross_fill": "Çapraz dolduruldu",
    "action.job_failed": "İş başarısız",
    "action.job_cancelled": "İş iptal edildi",
    "action.copy_browser": "Tarayıcı ile kopyalandı",
    "audit.titles.uidMigration": "Kayıtlar yeni sürüm için güncellendi: {analysis} analizindeki {records} kayıt artık sıralı kimlik kullanıyor",
}

STRINGS: dict[str, dict[str, str]] = {"en": _EN, "tr": _TR}


class _SafeParams(dict):
    """format_map helper: an unknown placeholder stays visible as ``{name}``."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def has_key(lang: str, key: str) -> bool:
    lang = norm_lang(lang)
    return key in STRINGS[lang] or key in STRINGS["en"]


def tr_(lang: Optional[str], key: str, /, **params: Any) -> str:
    """Translate ``key`` into ``lang``; falls back to English, then to the key itself."""
    lang = norm_lang(lang)
    text = STRINGS[lang].get(key)
    if text is None:
        text = STRINGS["en"].get(key)
    if text is None:
        return key
    if not params:
        return text
    try:
        return text.format_map(_SafeParams(params))
    except Exception:
        return text


# ─────────────────────────────────────────────────────────────────────────
#  Number / date formatting
# ─────────────────────────────────────────────────────────────────────────

def fmt_int(value: Any, lang: Optional[str]) -> str:
    """Integer with a thousands separator — en: 1,234 / tr: 1.234."""
    try:
        n = int(round(float(value or 0)))
    except Exception:
        n = 0
    s = f"{n:,}"
    return s.replace(",", ".") if norm_lang(lang) == "tr" else s


def fmt_float(value: Any, lang: Optional[str], digits: int = 1) -> str:
    """Decimal number — en: 1,234.5 / tr: 1.234,5."""
    try:
        x = float(value)
    except Exception:
        return str(value)
    s = f"{x:,.{digits}f}"
    if norm_lang(lang) == "tr":
        s = s.replace(",", "\0").replace(".", ",").replace("\0", ".")
    return s


def fmt_percent(ratio: Any, lang: Optional[str], digits: int = 1) -> str:
    """``ratio`` is a 0..1 fraction — en: 12.5% / tr: %12,5."""
    try:
        v = float(ratio) * 100.0
    except Exception:
        v = 0.0
    s = fmt_float(v, lang, digits)
    return f"%{s}" if norm_lang(lang) == "tr" else f"{s}%"


def fmt_dt(ts: Any, lang: Optional[str], *, seconds: bool = False) -> str:
    """Timestamp — en: 2026-11-04 14:30 / tr: 04.11.2026 14:30."""
    if not ts:
        return "—"
    try:
        d = dt.datetime.fromtimestamp(float(ts))
    except Exception:
        return "—"
    return fmt_datetime(d, lang, seconds=seconds)


def fmt_datetime(d: dt.datetime, lang: Optional[str], *, seconds: bool = False) -> str:
    tail = "%H:%M:%S" if seconds else "%H:%M"
    if norm_lang(lang) == "tr":
        return d.strftime("%d.%m.%Y " + tail)
    return d.strftime("%Y-%m-%d " + tail)


def fmt_value(value: Any, lang: Optional[str]) -> Any:
    """Localise a scalar detail value (numbers); everything else is returned unchanged."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return fmt_int(value, lang)
    if isinstance(value, float):
        return fmt_int(value, lang) if value.is_integer() else fmt_float(value, lang, 2)
    return value


# ─────────────────────────────────────────────────────────────────────────
#  Label helpers
# ─────────────────────────────────────────────────────────────────────────

def kind_label(lang: Optional[str], kind: str) -> str:
    key = f"kind.{kind}"
    return tr_(lang, key) if has_key(lang or "en", key) else kind


def user_action_label(lang: Optional[str], code: str) -> str:
    key = f"action.{code}"
    if has_key(lang or "en", key):
        return tr_(lang, key)
    if code.startswith("job_"):
        status_key = f"jobstatus.{code[4:]}"
        if has_key(lang or "en", status_key):
            return tr_(lang, status_key)
    return code


def detail_label(lang: Optional[str], key: str) -> str:
    k = f"detail.{key}"
    return tr_(lang, k) if has_key(lang or "en", k) else key


def filter_label(lang: Optional[str], key: str) -> str:
    k = f"filter.{key}"
    return tr_(lang, k) if has_key(lang or "en", k) else key


def field_source_label(lang: Optional[str], code: str) -> str:
    k = f"src.{code}"
    return tr_(lang, k) if has_key(lang or "en", k) else code


_STAGE_ALIASES = {
    "journal_vol_page": "journal_vol_pages",
    "borderline": "borderline_manual_review",
}


def _stage_key(raw: str) -> str:
    """'DOI exact' | '1_doi_exact' | '4_journal_vol_page' -> normalised stage key."""
    s = re.sub(r"^\d+_", "", str(raw or "").strip().lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return _STAGE_ALIASES.get(s, s)


def stage_label(lang: Optional[str], raw: str) -> str:
    """Match-stage name from an English stage label or a stage code; unknown values pass through."""
    k = f"stage.{_stage_key(raw)}"
    return tr_(lang, k) if has_key(lang or "en", k) else str(raw)


def resolve_title(lang: Optional[str], entry: dict[str, Any]) -> str:
    """Title of an audit entry in ``lang``.

    Uses the dictionary entry for ``title_key`` (with ``title_params``) when it
    exists; otherwise falls back to the stored (English) title.
    """
    key = entry.get("title_key")
    if key and has_key(lang or "en", str(key)):
        params = {
            k: fmt_value(v, lang) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
            for k, v in (entry.get("title_params") or {}).items()
        }
        return tr_(lang, str(key), **params)
    return str(entry.get("title") or "")
