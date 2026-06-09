"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, formatBytes, type MergeSummary, type AnalysisItem, type UploadedFile, translateApiError} from "@/lib/api-client";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { Button } from "@/components/Button";
import { JobProgress } from "@/components/JobProgress";
import { PageHeader } from "@/components/PageHeader";
import {
  ArrowRight, Combine, Download, FileText, Sparkles, FileSpreadsheet,
  AlertTriangle, CheckCircle2, Database, BarChart3, Zap, Clock, Layers, Info,
  Brain, ShieldCheck, ScrollText, History, ExternalLink,
  ChevronDown, ChevronRight, Check, X, BookOpen, HelpCircle, Cpu, Plus,
} from "lucide-react";
import { AuditLogPanel } from "@/components/AuditLogPanel";
import { ActiveAnalysisHero } from "@/components/ActiveAnalysisHero";
import { OtherAnalysesCollapse } from "@/components/OtherAnalysesCollapse";
import { UploadSection } from "@/components/UploadSection";
import { advancedKey } from "@/lib/use-pipeline";
import { useT, useI18n } from "@/lib/i18n";
import { cn } from "@/lib/cn";
import { useProjectId } from "@/lib/use-project-id";
import { StepNav } from "@/components/StepNav";

export default function MergePage() {
  const id = useProjectId();
  const t = useT();
  const { tArr, tObjArr } = useI18n();
  const [jobId, setJobId] = useState<string | null>(null);
  const [summary, setSummary] = useState<MergeSummary | null>(null);
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [analyses, setAnalyses] = useState<AnalysisItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showAudit, setShowAudit] = useState(false);
  /** "Yeni analiz" drawer açık mı */
  const [showAlgorithmCards, setShowAlgorithmCards] = useState(false);
  /** Akademik "Nasıl çalışır" detay modalı açık mı (gizli, linkle açılır) */
  const [showInfo, setShowInfo] = useState(false);
  /** "Yeni analiz" tıklandı → mevcut merge kompakt "old"a çekilir, UploadSection öne gelir */
  const [newAnalysisMode, setNewAnalysisMode] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [s, alist, f] = await Promise.all([
        api.mergeSummary(id),
        api.listAnalyses(id),
        api.listFiles(id),
      ]);
      setSummary(s);
      setAnalyses(alist.items);
      setActiveId(alist.active_id);
      setFiles(f);
    } catch (e) {
      setError(translateApiError(t, e));
    }
  }, [id]);

  useEffect(() => { refresh(); }, [refresh]);

  async function start() {
    setBusy(true); setError(null);
    try {
      const { job_id } = await api.startMerge(id);
      setJobId(job_id);
      // Job başladı → drawer'ı kapat (kullanıcı job progress'i görsün)
      setShowAlgorithmCards(false);
    } catch (e) {
      setError(translateApiError(t, e));
    } finally { setBusy(false); }
  }

  const handleJobComplete = useCallback(() => {
    refresh();
    setShowAlgorithmCards(false);
    setNewAnalysisMode(false);
    // Pipeline stepper'ı (ProjectNav) anında güncelle — Export/Report kilidi açılsın
    try { window.dispatchEvent(new Event("bibexpy:status")); } catch { /* ignore */ }
  }, [refresh]);

  /** Analiz silme/aktifleştirme sonrası — refresh + stepper/Next-Prev gating tazele
   * (son analiz silindiyse adımlar kilitlenir; aktif değişince analiz-bağlı advanced
   * yeniden okunur). */
  const handleChanged = useCallback(() => {
    refresh();
    try { window.dispatchEvent(new Event("bibexpy:status")); } catch { /* ignore */ }
  }, [refresh]);

  function handleNewAnalysis() {
    // Mevcut merge "old"a çekilir (kompakt) + UploadSection öne gelir; sayfayı başa al.
    setNewAnalysisMode(true);
    try { window.scrollTo({ top: 0, behavior: "smooth" }); } catch { /* ignore */ }
  }

  /** Header "Sonraki: Gelişmiş Düzenleme" → aktif analiz için advanced kilidini aç
   * (records/enrich açılır) + /records'a git. "Advanced Editing" kartıyla aynı eylem. */
  function unlockAdvancedForActive() {
    try {
      const aid = summary?.analysis?.id;
      if (aid) localStorage.setItem(advancedKey(id, aid), "1");
      window.dispatchEvent(new Event("bibexpy:status"));
    } catch { /* ignore */ }
  }

  const hasMerge = !!summary?.has_merge;
  const currentMethod = summary?.method;
  const activeAnalysis = analyses.find((a) => a.id === activeId) ?? null;
  const otherAnalyses = analyses.filter((a) => a.id !== activeId);
  // İlk birleştirme için kartlar inline, sonrasında drawer overlay olarak
  const cardsVisible = !hasMerge || showAlgorithmCards;
  const hasRaw = files.length > 0;
  const stale = !!summary?.stale;
  // Yükleme bölümünü öne al: bayat (otomatik, yeni ham dosya) veya kullanıcı "Yeni analiz" dedi (manuel)
  const uploadFirst = hasMerge && (stale || newAnalysisMode);

  return (
    <>
      <PageHeader
        title={t("analyses.title")}
        subtitle={t("analyses.subtitle")}
        badges={[{ label: t("analyses.stepBadge"), tone: "neutral" }]}
        right={
          <StepNav
            onHistory={() => setShowAudit(true)}
            nextOptions={hasMerge ? [
              { href: `/projects/${id}/export`, label: t("merge.next.downloadTitle"), tone: "brand" },
              { href: `/projects/${id}/records`, label: t("merge.next.advancedTitle"), onClick: unlockAdvancedForActive, tone: "emerald" },
            ] : null}
          />
        }
      />

      <div className="max-w-6xl mx-auto px-6 py-6 space-y-5">

        {error && (
          <div className="rounded-lg border border-danger/30 bg-danger-soft px-4 py-2 text-sm text-red-700">
            {error}
          </div>
        )}

        {jobId && (
          <JobProgress
            jobId={jobId}
            onComplete={handleJobComplete}
            onClose={() => setJobId(null)}
          />
        )}

        {/* ── YÜKLEME ÖNDE: bayat (yeni ham dosya) VEYA "Yeni analiz" tıklandı →
            UploadSection öne + mevcut merge KOMPAKT "old" satırı (Burak: "yeni analiz
            deyince mevcutu old'a çek, data & merge'e at") ── */}
        {uploadFirst && !jobId && (
          <div id="add-data">
            <UploadSection
              projectId={id}
              files={files}
              variant={stale ? "stale" : "ready"}
              onStartMerge={start}
              starting={busy || !!jobId}
              onChanged={refresh}
            />
          </div>
        )}
        {uploadFirst && activeAnalysis && (
          <div className="flex items-center gap-3 rounded-xl border border-border bg-bg-soft/50 px-4 py-3">
            <Brain className="h-5 w-5 flex-shrink-0 text-muted" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-ink">{activeAnalysis.label}</div>
              <div className="text-[11px] text-muted">
                {stale ? t("merge.oldMerge") : t("merge.currentMerge")} · {activeAnalysis.file_count} {t("common.files")}
              </div>
            </div>
            {!stale && (
              <button onClick={() => setNewAnalysisMode(false)} className="flex-shrink-0 text-xs text-muted underline hover:text-ink">
                {t("merge.backToActive")}
              </button>
            )}
            <Link href={`/projects/${id}/export`} className="flex-shrink-0 text-xs text-muted underline hover:text-ink">
              {t("merge.next.downloadTitle")}
            </Link>
          </div>
        )}

        {/* ── NORMAL: taze aktif merge: hero + sonraki adımlar ── */}
        {hasMerge && !uploadFirst && activeAnalysis && summary && (
          <ActiveAnalysisHero
            projectId={id}
            summary={summary}
            analysis={activeAnalysis}
            onNewAnalysis={handleNewAnalysis}
            onDeleted={handleChanged}
          />
        )}
        {hasMerge && !uploadFirst && summary && (
          <MergeNextButtons projectId={id} analysisId={summary.analysis?.id ?? ""} />
        )}

        {/* Geçmiş (aktif olmayan) analizler — collapse */}
        {otherAnalyses.length > 0 && (
          <OtherAnalysesCollapse
            projectId={id}
            items={otherAnalyses}
            busy={busy || !!jobId}
            onChanged={handleChanged}
          />
        )}

        {/* Akademik detay modalı — gizli; başlatma kartındaki "Nasıl çalışır" ile açılır */}
        <AlgorithmInfoModal
          open={showInfo}
          onClose={() => setShowInfo(false)}
          tone="success"
          icon={<Brain className="h-5 w-5" />}
          title={t("algoCard.smart.title")}
          tagline={t("algoCard.smart.tagline")}
          speed={t("algoCard.smart.speedHint")}
          details={{
            howItWorks: tArr("algoCard.smart.howItWorks"),
            useCases: tArr("algoCard.smart.useCases"),
            outputs: tObjArr<{ name: string; desc: string }>("algoCard.smart.outputs"),
            limitations: tArr("algoCard.smart.limitations"),
            citation: {
              title: t("algoCard.smart.citation.title"),
              journal: t("algoCard.smart.citation.journal"),
              related: tArr("algoCard.smart.citation.related"),
            },
          }}
          onStart={() => { setShowInfo(false); start(); }}
          buttonLabel={t("algoCard.startBtnSmart")}
          isActive={currentMethod === "smart"}
        />

        {/* ── İlk merge YOK → yükle + Smart Merge (birleşik "Veri & Birleştirme" akışı) ── */}
        {!hasMerge && !jobId && (
          <div id="add-data">
            <UploadSection
              projectId={id}
              files={files}
              variant={hasRaw ? "ready" : "empty"}
              onStartMerge={start}
              starting={busy}
              onChanged={refresh}
            />
          </div>
        )}

      </div>

      <AuditLogPanel
        projectId={id}
        open={showAudit}
        onClose={() => setShowAudit(false)}
      />
    </>
  );
}

/* ───────────────────── Algorithm card ───────────────────── */

type AlgorithmDetails = {
  howItWorks: string[];
  useCases: string[];
  outputs: { name: string; desc: string }[];
  limitations: string[];
  citation: { title: string; journal: string; related?: string[] } | null;
};

/* ───────────────────── Yeni Analiz Drawer / Inline ───────────────────── */
/**
 * Aktif analiz varsa algoritma kartlarını overlay (drawer) olarak göster —
 * ekran kararır, kullanıcı algoritma seçer veya kapatır.
 * Hiç analiz yoksa inline render et (ilk birleştirme deneyimi için).
 */
/**
 * Smart Merge başlatma kartı — tek algoritma için öne çıkan/güzel hero kart
 * (Burak: "ortada kötü gözüküyor, daha güzel yapalım").
 */
function SmartMergeStart({ onStart, onShowInfo, disabled, isActive }: {
  onStart: () => void;
  onShowInfo: () => void;
  disabled?: boolean;
  isActive: boolean;
}) {
  const t = useT();
  return (
    <div className="relative overflow-hidden rounded-2xl border border-success/30 bg-gradient-to-br from-success-soft/50 via-white to-white shadow-card">
      <div className="pointer-events-none absolute -right-10 -top-10 h-40 w-40 rounded-full bg-success/10 blur-3xl" />
      <div className="relative p-6 md:p-8">
        <div className="flex items-start gap-4">
          <div className="flex h-14 w-14 flex-shrink-0 items-center justify-center rounded-2xl bg-success text-white shadow-soft">
            <Brain className="h-7 w-7" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-bold text-ink">{t("algoCard.smart.title")}</h2>
              <span className="inline-flex items-center gap-1 rounded-full bg-success px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-white">
                <ShieldCheck className="h-3 w-3" /> {t("algoCard.academic")}
              </span>
            </div>
            <p className="mt-1 text-sm italic text-muted">{t("algoCard.smart.tagline")}</p>
            <p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink">{t("algoCard.smart.summary")}</p>
            <div className="mt-5 flex flex-wrap items-center gap-3">
              <Button
                onClick={onStart}
                disabled={disabled}
                className="bg-success px-6 text-base font-semibold hover:bg-success/90"
              >
                {isActive ? <Plus className="h-4 w-4" /> : <Combine className="h-4 w-4" />}
                {isActive ? t("algoCard.newAnalysisOfMethod", { method: "Smart" }) : t("algoCard.startBtnSmart")}
              </Button>
              <button
                type="button"
                onClick={onShowInfo}
                className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink"
              >
                <Info className="h-4 w-4" /> {t("algoCard.moreInfo")}
              </button>
              <span className="inline-flex items-center gap-1 rounded-full bg-bg-soft px-2.5 py-1 text-xs text-muted">
                <Clock className="h-3 w-3" /> {t("algoCard.smart.speedHint")}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * Merge sonrası 2 büyük buton (Burak): "Veriyi İndir" (→ Export) ve
 * "Gelişmiş Düzenleme" (→ Filtre; pipeline'da 3-4. adım kilidini açar).
 * Detay panelleri & dosya indirme bu sayfada GÖSTERİLMEZ.
 */
function MergeNextButtons({ projectId, analysisId }: { projectId: string; analysisId: string }) {
  const t = useT();
  function unlockAdvanced() {
    try {
      if (analysisId) localStorage.setItem(advancedKey(projectId, analysisId), "1");
      window.dispatchEvent(new Event("bibexpy:status"));
    } catch { /* ignore */ }
  }
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Link
        href={`/projects/${projectId}/export`}
        className="group flex flex-col items-center justify-center gap-2.5 rounded-2xl border-2 border-brand-200 bg-gradient-to-b from-brand-50/60 to-white px-6 py-9 text-center transition hover:border-brand-400 hover:shadow-card"
      >
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-brand-500 text-white shadow-soft transition group-hover:scale-105">
          <Download className="h-8 w-8" />
        </div>
        <div className="text-lg font-bold text-ink">{t("merge.next.downloadTitle")}</div>
        <div className="max-w-xs text-sm text-muted">{t("merge.next.downloadBody")}</div>
      </Link>
      <Link
        href={`/projects/${projectId}/records`}
        onClick={unlockAdvanced}
        className="group flex flex-col items-center justify-center gap-2.5 rounded-2xl border-2 border-border bg-white px-6 py-9 text-center transition hover:border-emerald-400 hover:shadow-card"
      >
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-emerald-500 text-white shadow-soft transition group-hover:scale-105">
          <Layers className="h-8 w-8" />
        </div>
        <div className="text-lg font-bold text-ink">{t("merge.next.advancedTitle")}</div>
        <div className="max-w-xs text-sm text-muted">{t("merge.next.advancedBody")}</div>
      </Link>
    </div>
  );
}


function NewAnalysisDrawer({
  asOverlay, onClose, children,
}: {
  asOverlay: boolean;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const t = useT();
  if (!asOverlay) {
    return <>{children}</>;
  }
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/45 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-6xl bg-white rounded-xl shadow-xl border border-border my-8 overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-border bg-bg-soft flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-brand-500 text-white flex items-center justify-center flex-shrink-0">
            <Plus className="h-4 w-4" />
          </div>
          <div className="flex-1">
            <h3 className="font-semibold text-ink text-base">{t("analyses.newAnalysisDrawerTitle")}</h3>
            <p className="text-[11px] text-muted mt-0.5">
              {t("analyses.newAnalysisDrawerHint")}
            </p>
          </div>
          <button
            onClick={onClose}
            className="h-8 w-8 inline-flex items-center justify-center rounded-md text-muted hover:bg-bg-soft"
            title={t("common.close")}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

function AlgorithmCard({
  tone, icon, title, tagline, summary, speed, details,
  onStart, disabled, isActive, buttonLabel, recommended, academic,
}: {
  tone: "brand" | "accent" | "success";
  icon: React.ReactNode;
  title: string;
  tagline: string;
  summary: string;
  speed: string;
  details: AlgorithmDetails;
  onStart: () => void;
  disabled: boolean;
  isActive: boolean;
  buttonLabel: string;
  recommended?: boolean;
  academic?: boolean;
}) {
  const t = useT();
  const [infoOpen, setInfoOpen] = useState(false);

  const palette = {
    brand:   { icon: "bg-brand-100 text-brand-700",   btn: "bg-brand-500 hover:bg-brand-600",     side: "bg-brand-500", text: "text-brand-700" },
    accent:  { icon: "bg-brand-500 text-white",        btn: "bg-brand-700 hover:bg-brand-800",     side: "bg-brand-700", text: "text-brand-800" },
    success: { icon: "bg-success text-white",          btn: "bg-success hover:bg-success/90",      side: "bg-success",   text: "text-emerald-800" },
  }[tone];

  return (
    <>
      <div
        className={cn(
          "relative rounded-2xl border bg-white overflow-hidden transition-all duration-200 flex flex-col",
          "hover:shadow-lg hover:-translate-y-0.5",
          isActive ? "border-success ring-2 ring-success/20 shadow-md" : "border-border shadow-sm",
        )}
      >
        {/* Sol kenar bar */}
        <div className={cn("absolute left-0 top-0 bottom-0 w-1", palette.side)} />

        {/* Rozetler — sağ üst */}
        <div className="absolute top-3 right-3 flex items-center gap-1.5 z-10">
          {isActive && (
            <span className="text-[9px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-success-soft text-emerald-700 flex items-center gap-1 border border-success/40">
              <CheckCircle2 className="h-2.5 w-2.5" /> {t("common.active")}
            </span>
          )}
          {recommended && !isActive && (
            <span className="text-[9px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-brand-500 text-white flex items-center gap-1">
              <Sparkles className="h-2.5 w-2.5" /> {t("algoCard.recommended")}
            </span>
          )}
          {academic && !recommended && !isActive && (
            <span className="text-[9px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-success text-white flex items-center gap-1">
              <ShieldCheck className="h-2.5 w-2.5" /> {t("algoCard.academic")}
            </span>
          )}
        </div>

        {/* İçerik — flex-col ile buton dibe sabit */}
        <div className="px-5 pl-6 py-5 flex flex-col flex-1">
          {/* Hero */}
          <div className="flex items-start gap-3 mb-3 pr-24">
            <div className={cn(
              "w-12 h-12 rounded-xl flex items-center justify-center flex-shrink-0",
              palette.icon,
            )}>
              {icon}
            </div>
            <div className="min-w-0 pt-0.5 flex-1">
              <h3 className="font-bold text-lg text-ink leading-tight">{title}</h3>
              <p className="text-xs text-muted italic mt-0.5">{tagline}</p>
            </div>
          </div>

          {/* Kısa özet — sabit yükseklik için min-h */}
          <p className="text-sm text-ink leading-relaxed mb-4 min-h-[60px]">
            {summary}
          </p>

          {/* Bilgi butonu + hız chip */}
          <div className="flex items-center gap-2 mb-4 flex-wrap">
            <button
              onClick={() => setInfoOpen(true)}
              className={cn(
                "text-xs px-2.5 py-1 rounded-full border-2 transition flex items-center gap-1.5 font-medium",
                "hover:shadow-md",
                tone === "brand"   ? "border-brand-200 bg-brand-50 text-brand-700 hover:bg-brand-100" :
                tone === "accent"  ? "border-brand-300 bg-brand-50 text-brand-800 hover:bg-brand-100" :
                                     "border-success/40 bg-success-soft text-emerald-700 hover:bg-success-soft",
              )}
            >
              <Info className="h-3.5 w-3.5" /> {t("algoCard.moreInfo")}
            </button>
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-bg-soft text-muted text-[11px]">
              <Clock className="h-3 w-3" /> {speed}
            </span>
          </div>

          {/* Buton — flex-1 sonrası mt-auto ile dibe */}
          <div className="mt-auto">
            <Button
              onClick={onStart}
              disabled={disabled}
              className={cn("w-full justify-center font-semibold", palette.btn)}
            >
              {isActive ? t("algoCard.newAnalysisOfMethod", { method: title.replace(" Merge", "") }) : buttonLabel}
            </Button>
            {isActive && (
              <p className="text-[10px] text-muted text-center mt-1.5 leading-tight">
                {t("algoCard.rerunNote")}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Detaylı bilgi modal'ı */}
      <AlgorithmInfoModal
        open={infoOpen}
        onClose={() => setInfoOpen(false)}
        tone={tone}
        icon={icon}
        title={title}
        tagline={tagline}
        speed={speed}
        details={details}
        onStart={onStart}
        buttonLabel={buttonLabel}
        isActive={isActive}
      />
    </>
  );
}


function AlgorithmInfoModal({
  open, onClose, tone, icon, title, tagline, speed, details, onStart, buttonLabel, isActive,
}: {
  open: boolean;
  onClose: () => void;
  tone: "brand" | "accent" | "success";
  icon: React.ReactNode;
  title: string;
  tagline: string;
  speed: string;
  details: AlgorithmDetails;
  onStart: () => void;
  buttonLabel: string;
  isActive: boolean;
}) {
  const t = useT();
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

  if (!open) return null;

  const palette = {
    brand:   { icon: "bg-brand-100 text-brand-700",   btn: "bg-brand-500 hover:bg-brand-600",     text: "text-brand-700",   accent: "text-brand-500" },
    accent:  { icon: "bg-brand-500 text-white",        btn: "bg-brand-700 hover:bg-brand-800",     text: "text-brand-800",   accent: "text-brand-700" },
    success: { icon: "bg-success text-white",          btn: "bg-success hover:bg-success/90",      text: "text-emerald-800", accent: "text-success" },
  }[tone];

  return (
    <>
      <div
        onClick={onClose}
        className="fixed inset-0 bg-ink/40 backdrop-blur-sm z-50"
      />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div
          className="bg-white rounded-2xl shadow-2xl flex flex-col overflow-hidden pointer-events-auto w-full max-w-3xl"
          style={{ maxHeight: "85vh" }}
        >
          {/* Header */}
          <div className="px-6 py-4 border-b border-border flex items-start gap-3">
            <div className={cn("w-11 h-11 rounded-xl flex items-center justify-center flex-shrink-0", palette.icon)}>
              {icon}
            </div>
            <div className="flex-1 min-w-0">
              <h2 className="font-bold text-xl text-ink">{title}</h2>
              <p className="text-sm text-muted italic">{tagline}</p>
              <div className="flex items-center gap-2 mt-1.5">
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-bg-soft text-muted text-[11px]">
                  <Clock className="h-3 w-3" /> {speed}
                </span>
                {isActive && (
                  <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-success-soft text-emerald-700 border border-success/40">
                    <CheckCircle2 className="h-2.5 w-2.5 inline mr-0.5" /> {t("analyses.activeBadge")}
                  </span>
                )}
              </div>
            </div>
            <button onClick={onClose} className="p-1.5 rounded-md hover:bg-bg-soft text-muted hover:text-ink">
              <X className="h-5 w-5" />
            </button>
          </div>

          {/* Body — scroll */}
          <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
            {/* Nasıl çalışır */}
            <section>
              <h3 className={cn("text-xs font-bold uppercase tracking-wider mb-2 flex items-center gap-1.5", palette.text)}>
                <Cpu className="h-3.5 w-3.5" /> {t("algoCard.howItWorks")}
              </h3>
              <ol className="space-y-1.5">
                {details.howItWorks.map((step, i) => (
                  <li key={i} className="text-sm text-ink flex gap-2.5 leading-relaxed">
                    <span className={cn(
                      "w-5 h-5 rounded-full text-[10px] font-bold flex items-center justify-center flex-shrink-0 mt-0.5",
                      tone === "brand"   ? "bg-brand-100 text-brand-700" :
                      tone === "accent"  ? "bg-brand-500 text-white" :
                                           "bg-success-soft text-emerald-700",
                    )}>{i + 1}</span>
                    <span>{step}</span>
                  </li>
                ))}
              </ol>
            </section>

            {/* Use cases */}
            <section>
              <h3 className={cn("text-xs font-bold uppercase tracking-wider mb-2 flex items-center gap-1.5", palette.text)}>
                <Check className="h-3.5 w-3.5" /> {t("algoCard.useCases")}
              </h3>
              <ul className="space-y-1.5">
                {details.useCases.map((u, i) => (
                  <li key={i} className="text-sm text-ink flex items-start gap-2">
                    <Check className={cn("h-3.5 w-3.5 mt-0.5 flex-shrink-0", palette.accent)} />
                    <span>{u}</span>
                  </li>
                ))}
              </ul>
            </section>

            {/* Çıktılar */}
            <section>
              <h3 className={cn("text-xs font-bold uppercase tracking-wider mb-2 flex items-center gap-1.5", palette.text)}>
                <FileSpreadsheet className="h-3.5 w-3.5" /> {t("algoCard.outputs")}
              </h3>
              <ul className="space-y-1.5">
                {details.outputs.map((o) => (
                  <li key={o.name} className="text-sm flex items-start gap-2 leading-relaxed">
                    <FileText className="h-3.5 w-3.5 mt-0.5 flex-shrink-0 text-muted" />
                    <span>
                      <code className="font-mono text-xs bg-bg-soft px-1.5 py-0.5 rounded">{o.name}</code>
                      <span className="text-muted ml-2 text-xs">— {o.desc}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </section>

            {/* Sınırlamalar */}
            {details.limitations.length > 0 && (
              <section>
                <h3 className="text-xs font-bold uppercase tracking-wider mb-2 flex items-center gap-1.5 text-warning">
                  <AlertTriangle className="h-3.5 w-3.5" /> {t("algoCard.limitations")}
                </h3>
                <ul className="space-y-1.5">
                  {details.limitations.map((l, i) => (
                    <li key={i} className="text-sm text-ink flex items-start gap-2">
                      <span className="text-warning mt-0.5 flex-shrink-0">•</span>
                      <span>{l}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* Akademik atıf */}
            {details.citation && (
              <section className="rounded-lg border border-success/30 bg-success-soft/40 px-4 py-3">
                <h3 className="text-xs font-bold uppercase tracking-wider mb-2 flex items-center gap-1.5 text-emerald-800">
                  <BookOpen className="h-3.5 w-3.5" /> {t("algoCard.citation")}
                </h3>
                <p className="text-sm text-ink leading-relaxed">
                  <strong>{details.citation.title}</strong> — {details.citation.journal}
                </p>
                {details.citation.related && details.citation.related.length > 0 && (
                  <details className="mt-2">
                    <summary className="text-xs text-emerald-800 cursor-pointer hover:underline">
                      {t("algoCard.related")} ({details.citation.related.length})
                    </summary>
                    <ul className="mt-1.5 space-y-0.5 pl-4">
                      {details.citation.related.map((r, i) => (
                        <li key={i} className="text-xs text-muted leading-relaxed">{r}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </section>
            )}
          </div>

          {/* Footer — başlat butonu */}
          <div className="px-6 py-3 border-t border-border flex items-center justify-between bg-bg-soft/30">
            <span className="text-xs text-muted">{t("algoCard.escClose")}</span>
            <Button
              onClick={() => { onStart(); onClose(); }}
              className={cn("font-semibold", palette.btn)}
            >
              {isActive ? `${buttonLabel} (${t("algoCard.rerun")})` : buttonLabel}
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}

/* ───────────────────── Summary header ───────────────────── */

function SummaryHeader({ summary }: { summary: MergeSummary }) {
  const t = useT();
  const m = summary.method;
  const methodLabel = m === "smart" ? "Smart" : m === "enhanced" ? "Enhanced" : "Simple";
  const methodColor = m === "smart"
    ? "bg-brand-50 border-brand-200 text-brand-700"
    : "bg-white border-border text-ink";
  const analysisLabel = summary.analysis?.label;
  const createdAt = summary.analysis?.created_at;
  const createdStr = createdAt
    ? new Date(createdAt * 1000).toLocaleString(undefined, {
        day: "2-digit", month: "2-digit", year: "numeric",
        hour: "2-digit", minute: "2-digit",
      })
    : null;
  return (
    <div className="rounded-xl bg-gradient-to-r from-brand-50 to-bg-soft border border-brand-200 px-4 py-3 flex items-center gap-3">
      <CheckCircle2 className="h-5 w-5 text-brand-600 flex-shrink-0" />
      <div className="flex-1">
        <div className="text-sm font-semibold text-ink flex items-center flex-wrap gap-2">
          <span>{t("merge.summaryHeader")}</span>
          {m && (
            <span className={cn(
              "text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded border",
              methodColor,
            )}>
              {methodLabel}
            </span>
          )}
          {analysisLabel && (
            <span className="text-xs font-normal text-muted truncate" title={analysisLabel}>
              · {analysisLabel}
            </span>
          )}
          {createdStr && (
            <span className="text-[11px] font-normal text-muted/80">
              · {createdStr}
            </span>
          )}
        </div>
        <div className="text-[11px] text-muted">
          {t("merge.summaryNote")}
        </div>
      </div>
    </div>
  );
}

/* ───────────────────── Smart-özel: Match Stages Panel ───────────────────── */

function MatchStagesPanel({ stages, conflictCount, fieldSourceDistribution, borderlinePending, borderlineTotal, projectId, onOpenAudit }: {
  stages: Record<string, number>;
  conflictCount: number;
  fieldSourceDistribution: Record<string, number>;
  borderlinePending: number;
  borderlineTotal: number;
  projectId: string;
  onOpenAudit: () => void;
}) {
  const t = useT();
  const totalMatches = Object.values(stages).reduce((s, n) => s + n, 0);
  const sortedStages = Object.entries(stages).sort((a, b) => b[1] - a[1]);
  const totalSources = Object.values(fieldSourceDistribution).reduce((s, n) => s + n, 0);
  const sortedSources = Object.entries(fieldSourceDistribution).sort((a, b) => b[1] - a[1]);

  return (
    <Card>
      <CardHeader>
        <Brain className="h-4 w-4 text-brand-600" />
        <h2 className="font-semibold text-sm flex-1">{t("merge.smart.panelTitle")}</h2>
        <span className="text-xs text-muted">{totalMatches} {t("merge.smart.matches")}</span>
      </CardHeader>
      <CardBody className="space-y-4">
        {/* Stage dağılımı */}
        <div>
          <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted mb-2 flex items-center gap-1.5">
            <Layers className="h-3 w-3" /> {t("merge.smart.matchStagesHeader")}
          </h3>
          <div className="space-y-1.5">
            {sortedStages.map(([label, count]) => {
              const pct = totalMatches > 0 ? (count / totalMatches) * 100 : 0;
              return (
                <div key={label} className="grid grid-cols-[200px_1fr_auto] gap-3 items-center text-xs">
                  <span className="text-ink truncate" title={label}>{label}</span>
                  <div className="h-1.5 rounded-full bg-bg-soft overflow-hidden">
                    <div className="h-full bg-brand-500" style={{ width: `${pct}%` }} />
                  </div>
                  <span className="font-semibold tabular-nums text-ink w-20 text-right">
                    {count.toLocaleString()} ({pct.toFixed(0)}%)
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Field source distribution (Caputo 2024 defaults uygulandı) */}
        {totalSources > 0 && (
          <div>
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted mb-2 flex items-center gap-1.5">
              <Database className="h-3 w-3" /> {t("merge.smart.fieldSourceHeader")}
              <span className="text-[10px] font-normal normal-case text-muted ml-1">— {t("merge.smart.fieldSourceNote", { conflicts: conflictCount })}</span>
            </h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              {sortedSources.map(([src, count]) => (
                <div key={src} className="rounded border border-border bg-bg-soft/40 px-2.5 py-1.5">
                  <div className="text-[10px] font-mono text-muted uppercase">{src}</div>
                  <div className="text-sm font-bold tabular-nums text-ink">{count.toLocaleString()}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Borderline durumu */}
        {borderlineTotal > 0 && (
          <div className="rounded-lg border border-warning/30 bg-warning-soft/40 px-3 py-2 flex items-center gap-2 text-xs">
            <AlertTriangle className="h-3.5 w-3.5 text-warning flex-shrink-0" />
            <span className="text-ink">
              {t("merge.smart.borderlineNote", { pending: borderlinePending, total: borderlineTotal })}
            </span>
          </div>
        )}

        {/* Detaylı rapor erişim — audit panel + indirilebilir Excel'ler */}
        <div className="rounded-lg border border-brand-200 bg-brand-50/40 px-3 py-2.5 space-y-2">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-brand-700 flex items-center gap-1">
            <ScrollText className="h-3 w-3" /> {t("merge.smart.detailedReport")}
          </div>
          <div className="flex flex-wrap gap-1.5 text-xs">
            <button
              onClick={onOpenAudit}
              className="px-2.5 py-1 rounded bg-white border border-border hover:border-brand-400 hover:bg-brand-50 text-ink flex items-center gap-1"
              title={t("nav.audit")}
            >
              <History className="h-3 w-3" /> {t("merge.smart.openAudit")}
            </button>
            <Link
              href={`/projects/${projectId}/report`}
              className="px-2.5 py-1 rounded bg-white border border-border hover:border-brand-400 hover:bg-brand-50 text-ink flex items-center gap-1"
            >
              <ScrollText className="h-3 w-3" /> {t("nav.report")}
              <ExternalLink className="h-2.5 w-2.5" />
            </Link>
            <a
              href={api.auditReportUrl(projectId)}
              target="_blank" rel="noreferrer"
              className="px-2.5 py-1 rounded bg-white border border-border hover:border-brand-400 hover:bg-brand-50 text-ink flex items-center gap-1"
              title={t("audit.downloadMarkdown")}
            >
              <Download className="h-3 w-3" /> {t("audit.downloadMarkdown")}
            </a>
            <a
              href={api.downloadFromUrl(projectId, "merged", "match_audit.xlsx")}
              className="px-2.5 py-1 rounded bg-white border border-border hover:border-brand-400 hover:bg-brand-50 text-ink flex items-center gap-1"
              title="match_audit.xlsx"
            >
              <FileSpreadsheet className="h-3 w-3" /> match_audit.xlsx
            </a>
            <a
              href={api.downloadFromUrl(projectId, "merged", "conflict_log.xlsx")}
              className="px-2.5 py-1 rounded bg-white border border-border hover:border-brand-400 hover:bg-brand-50 text-ink flex items-center gap-1"
              title="conflict_log.xlsx"
            >
              <FileSpreadsheet className="h-3 w-3" /> conflict_log.xlsx
            </a>
          </div>
          <p className="text-[10px] text-muted leading-relaxed">
            {t("merge.smart.detailedReportNote")}
          </p>
        </div>
      </CardBody>
    </Card>
  );
}

/* ───────────────────── General stats — 4 büyük kart ───────────────────── */

function GeneralStatsRow({ general }: { general: NonNullable<MergeSummary["general"]> }) {
  const t = useT();
  const dedupPct = Math.round(general.dedup_rate * 100);
  return (
    <div>
      <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted mb-2 flex items-center gap-1.5">
        <BarChart3 className="h-3 w-3" /> {t("merge.generalStats")}
      </h2>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatBigCard
          label={t("merge.stats.mergedRecords")}
          value={general.total_records.toLocaleString()}
          sub={`${general.merged_columns} ${t("merge.stats.columns")}`}
          tone="brand"
          icon={<Database className="h-4 w-4" />}
        />
        <StatBigCard
          label={t("merge.stats.wosInput")}
          value={general.wos_records.toLocaleString()}
          sub={`${general.common_columns} ${t("merge.stats.commonColumns")}`}
          tone="brand"
        />
        <StatBigCard
          label={t("merge.stats.scopusInput")}
          value={general.scopus_records.toLocaleString()}
          sub={`${t("merge.stats.totalInput")}: ${general.total_input.toLocaleString()}`}
          tone="brand"
        />
        <StatBigCard
          label={t("merge.stats.duplicatesRemoved")}
          value={general.duplicates_removed.toLocaleString()}
          sub={`${dedupPct}% ${t("merge.stats.dedupRate")}`}
          tone={dedupPct > 30 ? "success" : "warning"}
          icon={<Zap className="h-4 w-4" />}
        />
      </div>
    </div>
  );
}

function StatBigCard({ label, value, sub, tone, icon }: {
  label: string; value: string; sub?: string;
  tone: "brand" | "info" | "success" | "warning";
  icon?: React.ReactNode;
}) {
  const toneMap = {
    brand: "bg-brand-50 text-brand-700 border-brand-200",
    info: "bg-info-soft text-blue-700 border-info/30",
    success: "bg-success-soft text-emerald-700 border-success/30",
    warning: "bg-warning-soft text-amber-700 border-warning/30",
  };
  return (
    <div className={cn("rounded-xl border px-4 py-3", toneMap[tone])}>
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide opacity-80">
        {icon} {label}
      </div>
      <div className="text-2xl font-bold mt-1.5 tabular-nums">{value}</div>
      {sub && <div className="text-[10px] opacity-70 mt-0.5">{sub}</div>}
    </div>
  );
}

/* ───────────────────── Field stats table ───────────────────── */

// Not: Alan-bazlı doluluk istatistikleri "Hazırlık & Filtre" sayfasındaki
// Quality Dashboard'a taşındı (kavramsal olarak filtreleme/zenginleştirme adımı).
// Merge sayfası yalnızca teknik birleştirme kararlarını gösterir.

/* ───────────────────── Files panel ───────────────────── */

function FilesPanel({ files, projectId, lostWos, lostScopus }: {
  files: NonNullable<MergeSummary["files"]>;
  projectId: string;
  lostWos?: number;
  lostScopus?: number;
}) {
  const t = useT();
  const grouped: Record<string, typeof files> = {
    merged_dataset: [],
    lost_wos: [],
    lost_scopus: [],
    statistics: [],
    match_audit: [],
    conflict_log: [],
    borderline_queue: [],
    other: [],
  };
  for (const f of files) {
    const bucket = grouped[f.kind] ?? grouped.other;
    bucket.push(f);
  }

  const smartAuditFiles = [
    ...grouped.match_audit,
    ...grouped.conflict_log,
    ...grouped.borderline_queue,
  ];

  const lostParts: string[] = [];
  if (lostWos != null && lostWos > 0) lostParts.push(`${lostWos} WoS`);
  if (lostScopus != null && lostScopus > 0) lostParts.push(`${lostScopus} Scopus`);

  return (
    <Card>
      <CardHeader>
        <FileSpreadsheet className="h-4 w-4 text-brand-500" />
        <h2 className="font-semibold text-sm flex-1">{t("merge.files.title")}</h2>
        <span className="text-xs text-muted">{files.length} {t("common.files")}</span>
      </CardHeader>
      <CardBody className="space-y-3">
        {grouped.merged_dataset.length > 0 && (
          <FileGroup
            title={t("merge.files.mainDataset")}
            icon={<Database className="h-3.5 w-3.5 text-brand-500" />}
            files={grouped.merged_dataset}
            projectId={projectId}
            tone="brand"
          />
        )}
        {(grouped.lost_wos.length > 0 || grouped.lost_scopus.length > 0) && (
          <FileGroup
            title={t("merge.files.lostRecords")}
            icon={<AlertTriangle className="h-3.5 w-3.5 text-warning" />}
            files={[...grouped.lost_wos, ...grouped.lost_scopus]}
            projectId={projectId}
            tone="warning"
            extraNote={
              lostParts.length > 0 ? (
                <span className="text-[11px] text-muted">
                  {t("merge.files.lostNote", { wos: lostWos ?? 0, scopus: lostScopus ?? 0 })}
                </span>
              ) : null
            }
          />
        )}
        {grouped.statistics.length > 0 && (
          <FileGroup
            title={t("merge.files.stats")}
            icon={<BarChart3 className="h-3.5 w-3.5 text-info" />}
            files={grouped.statistics}
            projectId={projectId}
            tone="info"
          />
        )}
        {smartAuditFiles.length > 0 && (
          <FileGroup
            title={t("merge.files.smartAudit")}
            icon={<ShieldCheck className="h-3.5 w-3.5 text-success" />}
            files={smartAuditFiles}
            projectId={projectId}
            tone="success"
            extraNote={
              <span className="text-[11px] text-muted">
                {t("merge.files.smartAuditNote")}
              </span>
            }
          />
        )}
        {grouped.other.length > 0 && (
          <FileGroup
            title={t("merge.files.other")}
            icon={<FileText className="h-3.5 w-3.5 text-muted" />}
            files={grouped.other}
            projectId={projectId}
            tone="info"
          />
        )}
      </CardBody>
    </Card>
  );
}

function FileGroup({ title, icon, files, projectId, tone, extraNote }: {
  title: string;
  icon: React.ReactNode;
  files: NonNullable<MergeSummary["files"]>;
  projectId: string;
  tone: "brand" | "warning" | "info" | "success";
  extraNote?: React.ReactNode;
}) {
  const t = useT();
  const toneBorder = {
    brand: "border-brand-200 bg-brand-50/30",
    warning: "border-warning/30 bg-warning-soft/30",
    info: "border-info/30 bg-info-soft/30",
    success: "border-success/30 bg-success-soft/30",
  }[tone];
  return (
    <div className={cn("rounded-lg border px-3 py-2", toneBorder)}>
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted mb-1.5">
        {icon} {title}
      </div>
      {extraNote && <div className="mb-1.5">{extraNote}</div>}
      <ul className="space-y-1">
        {files.map((f) => (
          <li key={f.name} className="flex items-center gap-2 text-xs py-0.5">
            <FileText className="h-3.5 w-3.5 text-muted" />
            <span className="flex-1 truncate text-ink">{f.name}</span>
            <span className="text-[10px] text-muted tabular-nums w-16 text-right">
              {formatBytes(f.size)}
            </span>
            <a
              href={api.downloadFromUrl(projectId, "merged", f.name)}
              className="text-muted hover:text-brand-600 p-0.5"
              title={t("common.download")}
            >
              <Download className="h-3.5 w-3.5" />
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
