"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, type MergeSummary, type AnalysisItem, type UploadedFile, translateApiError} from "@/lib/api-client";
import { Button } from "@/components/Button";
import { JobProgress } from "@/components/JobProgress";
import { PageHeader } from "@/components/PageHeader";
import {
  Download, FileText, FileSpreadsheet, AlertTriangle, CheckCircle2,
  Clock, Layers, Brain, Check, X, BookOpen, Cpu, Loader2,
} from "lucide-react";
import { AuditLogPanel } from "@/components/AuditLogPanel";
import { ActiveAnalysisHero } from "@/components/ActiveAnalysisHero";
import { OtherAnalysesCollapse } from "@/components/OtherAnalysesCollapse";
import { UploadSection } from "@/components/UploadSection";
import { BorderlineReviewPanel } from "@/components/BorderlineReviewPanel";
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
  /** Akademik "Nasıl çalışır" detay modalı açık mı (gizli, linkle açılır) */
  const [showInfo, setShowInfo] = useState(false);
  /** "Yeni analiz" tıklandı → mevcut merge kompakt "old"a çekilir, UploadSection öne gelir */
  const [newAnalysisMode, setNewAnalysisMode] = useState(false);
  /** İlk veri çekimi sürerken true — summary null "merge yok" değil "henüz bilinmiyor"
   * demektir; bu olmadan boş upload ekranı yanıp sonra aktif merge'e atlardı. */
  const [initialLoading, setInitialLoading] = useState(true);

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
    } finally {
      setInitialLoading(false);
    }
  }, [id]);

  useEffect(() => { refresh(); }, [refresh]);

  async function start() {
    setBusy(true); setError(null);
    try {
      const { job_id } = await api.startMerge(id);
      setJobId(job_id);
    } catch (e) {
      setError(translateApiError(t, e));
    } finally { setBusy(false); }
  }

  const handleJobComplete = useCallback(() => {
    refresh();
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

        {/* İlk veri çekimi sürerken loader — summary daha gelmeden boş upload
            ekranını gösterip sonra aktif merge'e atlama (flash) sorununu önler. */}
        {initialLoading && !jobId && (
          <div className="rounded-xl border border-border bg-bg-card shadow-card px-6 py-16 flex flex-col items-center justify-center gap-3 text-muted">
            <Loader2 className="h-6 w-6 animate-spin text-brand-500" />
            <span className="text-sm">{t("common.loading")}</span>
          </div>
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
        {/* Belirsiz çiftler — Smart Merge kesin karar veremediği çiftleri burada,
            birleştirme adımında sorar: önce "kontrol etmek ister misiniz?" uyarısı,
            "Evet" ile inceleme açılır. Hiç bekleyen belirsizlik yoksa görünmez. */}
        {hasMerge && !uploadFirst && (
          <BorderlineReviewPanel
            key={summary?.analysis?.id ?? "none"}
            projectId={id}
            onApplied={refresh}
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

        {/* ── İlk merge YOK → yükle + Smart Merge (birleşik "Veri & Birleştirme" akışı) ──
            initialLoading bitene kadar gösterme: aksi halde "merge yok" sanıp boş ekran çıkar. */}
        {!hasMerge && !jobId && !initialLoading && (
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
