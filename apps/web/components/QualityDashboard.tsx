"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, translateApiError, translateTitle, type QualityStats, type FillReport } from "@/lib/api-client";
import { Card, CardBody, CardHeader } from "./Card";
import {
  Heart, BarChart3, Sparkles, AlertCircle, CheckCircle2, Loader2, Play, X, FileText,
  Table2, Download, Server,
} from "lucide-react";
import Link from "next/link";
import { toPng } from "html-to-image";
import { useI18n, useT, type Locale } from "@/lib/i18n";
import { formatNumber } from "@/lib/i18n/format";
import { useConfirm, useToast } from "./Dialogs";
import { cn } from "@/lib/cn";
import {
  cancelJob, isBusyError, jobErrorText, jobOwnerHref, jobStatusLine, refreshProjectJobs,
  start as startTrackedJob, useJobFinished, useProjectBusy, useProjectJobs, getProjectJobs, type StoreJob,
} from "@/lib/job-store";

type Props = {
  projectId: string;
  onChanged?: () => void;          // refresh the outside (the table) when filling finishes
};

/**
 * DOI-lookup part of the fill report. The report type in api-client.ts may not list the
 * newer provenance fields yet, so they are declared here (all optional, defensive).
 */
type DoiReport = {
  scanned?: number;
  filled?: number;
  skipped?: Record<string, number>;
  sources_exhausted?: string[];
  provenance?: string | null;
};

/** Percent of a 0..1 ratio in the app language ("42%" / "%42"). */
function fmtPct(ratio: number, locale: Locale): string {
  return formatNumber(ratio, locale, { style: "percent", maximumFractionDigits: 0 });
}

/** URL of the DOI lookup log CSV, if the api client provides it (added by the DOI work package). */
function doiLogUrl(projectId: string): string | null {
  const fn = (api as unknown as { doiLogUrl?: (id: string) => string }).doiLogUrl;
  return typeof fn === "function" ? fn(projectId) : null;
}

/**
 * Data Quality & Filling panel (Harmonization step).
 *
 * A single "Fill missing fields" flow: ONE API call per DOI (all empty fields)
 * + the ML pass (DE/ID/SC/WC) → one job, one progress bar. Field-by-field,
 * one-at-a-time filling was removed (it was a redundant repeat API call).
 */
export function QualityDashboard({ projectId, onChanged }: Props) {
  const t = useT();
  const { locale } = useI18n();
  const confirm = useConfirm();
  const toast = useToast();
  const [stats, setStats] = useState<QualityStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(true);
  // Past 'Fill all' report (from the audit) — survives a page refresh.
  const [savedReport, setSavedReport] = useState<FillReport | null>(null);
  // Separate in-flight states for the job start request (until job_id returns) and
  // the cancel request — covers the short network delay before the 'running' state
  // arrives over SSE.
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  // The last fill that ended badly (failed / server restarted); shown until the next start.
  const [failedJob, setFailedJob] = useState<StoreJob | null>(null);

  // Job tracking lives in the shared store: it survives navigation, owns the EventSource
  // and reconnects on its own. A fill-all job that is already running when this card
  // mounts (user left the page and came back, or reloaded) is simply found here.
  const { active: activeJobs, ready: jobsReady } = useProjectJobs(projectId);
  // Prefer the "Fill all" job; /enrich/api shares the kind but has another title_key.
  const job: StoreJob | null =
    activeJobs.find((j) => j.kind === "enrich_api" && j.title_key === "audit.titles.fillAll")
    ?? activeJobs.find((j) => j.kind === "enrich_api")
    ?? null;
  // Any OTHER exclusive job of the project (e.g. a disambiguation scan) blocks a new fill.
  const blockingJob = activeJobs.find((j) => j.exclusive && j.id !== job?.id) ?? null;
  // Also covers a job that was just started and is still an anonymous placeholder in the store.
  const { busy: jobBusy } = useProjectBusy(projectId);

  const refreshStats = useCallback(async () => {
    try {
      setStats(await api.qualityStats(projectId));
    } catch { /* silent */ }
  }, [projectId]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api.qualityStats(projectId)
      .then((s) => { if (alive) { setStats(s); setLoading(false); } })
      .catch(() => { if (alive) setLoading(false); });
    // Fetch the past report — if present, the report is shown instead of the banner.
    api.lastFillReport(projectId)
      .then((r) => { if (alive) setSavedReport(r.report); })
      .catch(() => {});
    return () => { alive = false; };
  }, [projectId]);

  // React to the end of an enrichment job (also one that finished while another page was
  // open: the store announces it and this card re-reads stats and the saved report).
  useJobFinished((e) => {
    if (e.job.project_id !== projectId || e.job.kind !== "enrich_api") return;
    void refreshStats();
    onChanged?.();
    if (e.outcome === "completed") {
      setFailedJob(null);
      // The persistent report comes from the audit log.
      api.lastFillReport(projectId).then((r) => setSavedReport(r.report)).catch(() => {});
    } else if (e.outcome === "failed" || e.outcome === "lost") {
      setFailedJob(e.job);
    } else if (e.outcome === "cancelled") {
      // The worker still flushes its partial result after the status flips to cancelled,
      // so read the stats again a little later as well.
      setTimeout(() => { void refreshStats(); }, 2000);
      setTimeout(() => {
        void refreshStats();
        api.lastFillReport(projectId).then((r) => setSavedReport(r.report)).catch(() => {});
      }, 6000);
    }
  });

  const running = !!job;

  async function startFillAll() {
    if (running || !stats || starting || blockingJob || (jobBusy && !job)) return;
    const ok = await confirm({
      title: t("records.quality.startAllTitle"),
      message: t("records.quality.startAllConfirm", { n: formatNumber(fillableCount, locale) }),
      detail: t("records.quality.startAllFreeNote"),
      confirmLabel: t("records.quality.startAll"),
    });
    if (!ok) return;
    setStarting(true);
    setFailedJob(null);
    try {
      await startTrackedJob(projectId, api.startFillAll(projectId));
    } catch (e) {
      if (isBusyError(e)) {
        // Another job holds the project (often this very fill, forgotten by the UI): look it up.
        await refreshProjectJobs(projectId);
        const existing = getProjectJobs(projectId).active.find((j) => j.kind === "enrich_api");
        if (existing) toast(t("records.quality.alreadyRunningAttached"), { tone: "info" });
        else toast(translateApiError(t, e, "records.quality.startFailed"), { tone: "danger" });
      } else {
        toast(translateApiError(t, e, "records.quality.startFailed"), { tone: "danger" });
      }
    } finally { setStarting(false); }
  }

  async function cancelFillAll() {
    if (!job || cancelling) return;
    setCancelling(true);
    try { await cancelJob(job.id); }
    catch (e) { toast(translateApiError(t, e), { tone: "danger" }); }
    finally { setCancelling(false); }
  }

  if (loading) {
    return (
      <Card>
        <CardBody className="text-center py-8 text-muted text-sm">{t("records.quality.loadingQuality")}</CardBody>
      </Card>
    );
  }
  if (!stats) return null;

  const healthPct = Math.round(stats.health_score * 100);
  const healthColor = healthPct >= 90 ? "text-emerald-300" : healthPct >= 70 ? "text-amber-300" : "text-red-300";

  const filledHigh = stats.fields.filter((f) => f.available && f.fill_rate >= 0.9);
  const filledMid = stats.fields.filter((f) => f.available && f.fill_rate >= 0.5 && f.fill_rate < 0.9);
  const filledLow = stats.fields.filter((f) => f.available && f.fill_rate < 0.5);
  const fillableCount = stats.fields.filter((f) => f.available && f.missing > 0).length;
  const jobPct = fmtPct(job?.progress ?? 0, locale);
  const jobBar = Math.round((job?.progress ?? 0) * 100);
  // Translated one-line status from the backend (status_key); fallback = last log line.
  const statusLine = job ? (jobStatusLine(t, job, locale) ?? t("records.quality.starting")) : "";
  const failedText = failedJob ? jobErrorText(t, failedJob) : null;
  const doiReport = (savedReport?.doi ?? null) as DoiReport | null;
  const blockingHref = blockingJob ? jobOwnerHref(blockingJob, projectId) : null;
  // DOI lookup outcome: candidates that were found but NOT written (ambiguous, preprint only,
  // duplicate, ...). "no_match" is the normal outcome for most records and stays out of the headline.
  const skippedEntries = Object.entries(doiReport?.skipped ?? {})
    .filter(([reason, n]) => reason !== "no_match" && Number(n) > 0);
  const skippedTotal = skippedEntries.reduce((s, [, n]) => s + Number(n), 0);
  const skippedBreakdown = skippedEntries
    .map(([reason, n]) => {
      const key = `records.quality.skip.${reason}`;
      const label = t(key);
      return `${label === key ? reason : label}: ${formatNumber(Number(n), locale)}`;
    })
    .join(" · ");
  const provenanceUrl = doiReport?.provenance ? doiLogUrl(projectId) : null;
  const openalexExhausted = (doiReport?.sources_exhausted ?? []).includes("openalex");
  // Do not offer a new run before the first job lookup finished or while a job holds the project.
  const lockedByJob = jobBusy && !job;
  const startDisabled = fillableCount === 0 || starting || !!blockingJob || lockedByJob || !jobsReady;

  return (
    <Card>
      <CardHeader>
        <Heart className={cn("h-4 w-4", healthColor)} />
        <h2 className="font-semibold text-sm flex-1">{t("records.quality.title")}</h2>
        <div className="flex items-center gap-2">
          <span className={cn("text-xs font-semibold tabular-nums", healthColor)} title={t("records.quality.healthScoreHint")}>
            {t("records.quality.healthScore")} {fmtPct(stats.health_score, locale)}
          </span>
          <button onClick={() => setExpanded(!expanded)} className="text-xs text-white/70 hover:text-white">
            {expanded ? t("common.close").toLowerCase() : t("common.open").toLowerCase()}
          </button>
        </div>
      </CardHeader>
      {expanded && (
        <CardBody className="space-y-4">
          {/* Data health scan — a prominent fill CTA. Shown while running (running) or
              when there is no report yet (no savedReport). If a past report exists,
              the persistent report below is shown instead of the banner. */}
          {(running || !savedReport || !!failedJob) && (
          <div className={cn(
            "rounded-xl p-4 text-white shadow-lg ring-1 transition",
            failedJob && !running ? "bg-gradient-to-r from-rose-500 to-red-600 ring-red-300/40"
              : "bg-gradient-to-r from-cyan-500 to-brand-600 ring-cyan-300/40",
          )}>
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-xl bg-white/15 flex items-center justify-center flex-shrink-0">
                {running ? <Loader2 className="h-6 w-6 animate-spin" />
                  : failedJob ? <AlertCircle className="h-6 w-6" />
                  : <Sparkles className="h-6 w-6" />}
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="text-base font-extrabold leading-tight">{t("records.quality.scanTitle")}</h3>
                <p className="text-xs text-white/85 mt-0.5 truncate" title={running ? statusLine : failedText ?? undefined}>
                  {running ? statusLine
                    : failedText ? failedText
                    : fillableCount > 0 ? t("records.quality.scanDesc", { n: formatNumber(fillableCount, locale) })
                    : t("records.quality.scanAllGood")}
                </p>
              </div>
              {running ? (
                <div className="flex items-center gap-3 flex-shrink-0">
                  <span className="text-2xl font-extrabold tabular-nums">{jobPct}</span>
                  <button
                    onClick={cancelFillAll}
                    disabled={cancelling}
                    className="flex items-center gap-1.5 rounded-lg bg-white/15 hover:bg-white/25 px-3 py-2 text-sm font-semibold transition disabled:opacity-60"
                  >
                    {cancelling ? <Loader2 className="h-4 w-4 animate-spin" /> : <X className="h-4 w-4" />}
                    {t("records.quality.stopAll")}
                  </button>
                </div>
              ) : (
                <button
                  onClick={startFillAll}
                  disabled={startDisabled}
                  title={lockedByJob ? t("lock.busyReason") : t("records.quality.startAllHint")}
                  className="group flex items-center gap-2.5 rounded-xl bg-white px-5 py-3 text-cyan-700 font-extrabold shadow-md ring-1 ring-white/60 transition hover:scale-[1.03] hover:shadow-xl disabled:opacity-60 disabled:cursor-not-allowed disabled:hover:scale-100 flex-shrink-0"
                >
                  {starting ? <Loader2 className="h-5 w-5 animate-spin" /> : <Play className="h-5 w-5 fill-current" />}
                  <span>{t("records.quality.startAll")}</span>
                  {fillableCount > 0 && <span className="ml-1 rounded-lg bg-cyan-100 px-2 py-0.5 text-sm tabular-nums">{formatNumber(fillableCount, locale)}</span>}
                </button>
              )}
            </div>
            {running && (
              <>
                <div className="h-1.5 mt-3 rounded-full bg-white/20 overflow-hidden">
                  <div className="h-full bg-white transition-all" style={{ width: `${jobBar}%` }} />
                </div>
                <p className="mt-2 flex items-center gap-1.5 text-[11px] text-white/80">
                  <Server className="h-3 w-3 flex-shrink-0" />
                  {t("records.quality.backgroundNote")}
                </p>
              </>
            )}
          </div>
          )}

          {/* Another exclusive job (e.g. a disambiguation scan) holds the project: say which. */}
          {blockingJob && (
            <div className="rounded-lg border border-cyan-200 bg-cyan-50 px-4 py-2 text-xs text-cyan-900 flex items-center gap-2">
              <Loader2 className="h-3.5 w-3.5 flex-shrink-0 animate-spin" />
              <span className="flex-1">
                {t("records.quality.blockedBy", { title: translateTitle(t, blockingJob) || t("jobs.running") })}
              </span>
              {blockingHref && (
                <Link href={blockingHref} className="font-semibold underline whitespace-nowrap">
                  {t("jobs.banner.open")}
                </Link>
              )}
            </div>
          )}

          {/* Persistent enrichment report — if 'Fill all' was run in the past (savedReport),
              this is shown instead of the banner when not running. "Run again" re-runs it. */}
          {savedReport && !running && (
            <div className="rounded-xl border border-success/40 bg-success-soft/30 p-4">
              <div className="flex items-start gap-3">
                <div className="w-10 h-10 rounded-lg bg-success/15 text-success flex items-center justify-center flex-shrink-0">
                  <CheckCircle2 className="h-5 w-5" />
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="font-bold text-sm text-ink">{t("records.quality.reportTitle")}</h3>
                  <p className="text-xs text-muted mt-0.5">
                    {savedReport.api?.total != null
                      ? t("records.quality.reportSummary", {
                          enriched: formatNumber(savedReport.enriched ?? 0, locale),
                          scanned: formatNumber(savedReport.api.total, locale),
                        })
                      : t("records.quality.reportSummaryShort", { enriched: formatNumber(savedReport.enriched ?? 0, locale) })}
                  </p>
                  {(doiReport?.filled ?? 0) > 0 && (
                    <p className="text-xs font-medium text-emerald-700 mt-0.5">
                      {t("records.quality.reportDois", { n: formatNumber(doiReport!.filled!, locale) })}
                    </p>
                  )}
                  {skippedTotal > 0 && (
                    <p className="text-xs text-amber-700 mt-0.5">
                      {t("records.quality.doiSkipped", {
                        n: formatNumber(skippedTotal, locale),
                        reasons: skippedBreakdown,
                      })}
                      {provenanceUrl && (
                        <>
                          {" "}
                          <a href={provenanceUrl} rel="noopener" className="font-semibold underline">
                            {t("records.quality.doiLogDownload")}
                          </a>
                        </>
                      )}
                    </p>
                  )}
                  {openalexExhausted && (
                    <p className="text-xs text-amber-700 mt-0.5">
                      {t("records.quality.openalexExhausted")}{" "}
                      <Link href="/settings" className="font-semibold underline">
                        {t("records.quality.openSettings")}
                      </Link>
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <button onClick={startFillAll} disabled={startDisabled}
                    title={lockedByJob ? t("lock.busyReason") : undefined}
                    className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-cyan-500 text-white hover:bg-cyan-600 disabled:opacity-50 flex items-center gap-1.5">
                    {starting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3 fill-current" />}
                    {t("records.quality.runAgain")}
                    {fillableCount > 0 && <span className="rounded bg-white/20 px-1 tabular-nums">{formatNumber(fillableCount, locale)}</span>}
                  </button>
                </div>
              </div>
              {savedReport.per_field_fill && (
                <div className="mt-3 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-1.5">
                  {Object.entries(savedReport.per_field_fill).map(([f, v]) => {
                    const b = Math.round((v.before ?? 0) * 100);
                    const a = Math.round((v.after ?? 0) * 100);
                    const gained = a > b;
                    return (
                      <div key={f} className={cn("rounded-md border bg-white px-2 py-1 text-[11px] flex items-center gap-1.5", gained ? "border-success/60" : "border-border")}>
                        <span className="font-mono text-[10px] text-muted">{f}</span>
                        <span className="ml-auto tabular-nums text-ink">
                          {fmtPct(b / 100, locale)} → <span className={gained ? "font-bold text-emerald-700" : "text-muted"}>{fmtPct(a / 100, locale)}</span>
                          {gained && <span className="ml-1 font-bold text-emerald-700">+{a - b}</span>}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
              <p className="text-[10px] text-muted mt-2">
                {t("records.quality.reportOverall", { before: fmtPct(savedReport.fill_rate_before ?? 0, locale), after: fmtPct(savedReport.fill_rate_after ?? 0, locale) })}
                {savedReport.snapshot ? ` · ${t("disambiguate.snapshot")}: ${String(savedReport.snapshot).split(/[\\/]/).pop()}` : ""}
              </p>
            </div>
          )}

          {/* Summary cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard icon={<FileText className="h-4 w-4" />} label={t("records.quality.totalRecords")}
              value={formatNumber(stats.total_records, locale)} accent="brand" />
            <StatCard icon={<CheckCircle2 className="h-4 w-4" />} label={t("records.quality.highFill")}
              value={formatNumber(filledHigh.length, locale)} hint="≥90%" accent="success" />
            <StatCard icon={<AlertCircle className="h-4 w-4" />} label={t("records.quality.midFill")}
              value={formatNumber(filledMid.length, locale)} hint="50–90%" accent="warning" />
            <StatCard icon={<AlertCircle className="h-4 w-4" />} label={t("records.quality.lowFill")}
              value={formatNumber(filledLow.length, locale)} hint="<50%" accent="danger" />
          </div>

          {/* Per-field fill rate — a single bar: each field gets an equal slice; filled=green (left), missing=field color (right) */}
          <div>
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted flex items-center gap-1 mb-2">
              <BarChart3 className="h-3 w-3" /> {t("records.quality.fieldFillRates")}
            </h3>
            {(() => {
              const fields = stats.fields.filter((f) => f.available);
              const n = fields.length || 1;
              const w = 100 / n;                                            // each field's equal share of the bar
              const greenPct = fields.reduce((s, f) => s + w * f.fill_rate, 0); // all filled shares in a single pool
              const missing = fields.filter((f) => f.fill_rate < 1);
              return (
                <div className="flex h-7 rounded-md overflow-hidden border border-border mb-1.5">
                  {/* Left: a SINGLE pool of all filled fields — green block, with the total fill on top */}
                  <div
                    className="h-full bg-emerald-500 flex items-center justify-center"
                    style={{ width: `${greenPct}%` }}
                    title={`${fmtPct(greenPct / 100, locale)} ${t("records.quality.filledShort")}`}
                  >
                    <span className="font-mono text-[10px] font-bold text-white whitespace-nowrap drop-shadow-[0_1px_1px_rgba(0,0,0,0.5)]">
                      {fmtPct(greenPct / 100, locale)}
                    </span>
                  </div>
                  {/* Right: each field's missing share — stacked by field color */}
                  {missing.map((f) => (
                    <div
                      key={f.field}
                      className="h-full border-l border-white/70"
                      style={{ width: `${w * (1 - f.fill_rate)}%`, backgroundColor: fieldColor(f.field) }}
                      title={`${f.field} · ${fieldLabel(t, f.field, f.label)} — ${fmtPct(1 - f.fill_rate, locale)} ${t("records.quality.missingShort")}`}
                    />
                  ))}
                </div>
              );
            })()}
            <p className="text-[10px] text-muted mb-2">{t("records.quality.barLegend")}</p>
            <div className="flex flex-wrap gap-1.5">
              {stats.fields.filter((f) => f.available).map((f) => (
                <FieldChip key={f.field} field={f} />
              ))}
            </div>
            <p className="text-[10px] text-muted mt-2 leading-relaxed">
              <Sparkles className="h-3 w-3 inline mr-0.5 text-brand-500" />
              {t("records.quality.fillHint")}
            </p>
          </div>

          {/* #6: Biblioshiny-style Overview table + CSV/XLSX/PNG download */}
          <QualityOverview projectId={projectId} stats={stats} />
        </CardBody>
      )}
    </Card>
  );
}

/** Overview — per-field summary table + download (CSV/XLSX from the backend, PNG client-side). */
function QualityOverview({ projectId, stats }: { projectId: string; stats: QualityStats }) {
  const t = useT();
  const { locale } = useI18n();
  const ref = useRef<HTMLDivElement>(null);
  const fields = stats.fields.filter((f) => f.available);

  function downloadFile(fmt: "csv" | "xlsx") {
    const a = document.createElement("a");
    a.href = api.qualityOverviewUrl(projectId, fmt);
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  async function downloadPng() {
    if (!ref.current) return;
    try {
      const dataUrl = await toPng(ref.current, { backgroundColor: "#ffffff", pixelRatio: 2 });
      const a = document.createElement("a");
      a.href = dataUrl;
      a.download = "data_health.png";
      a.click();
    } catch { /* silent if the image cannot be generated */ }
  }

  const o = (k: string) => t(`records.quality.overview.${k}`);

  return (
    <div>
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted flex items-center gap-1 flex-1">
          <Table2 className="h-3 w-3" /> {o("title")}
        </h3>
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-muted flex items-center gap-1"><Download className="h-3 w-3" /> {o("download")}:</span>
          <button onClick={() => downloadFile("csv")} className="text-[11px] font-medium px-2 py-0.5 rounded border border-border bg-white hover:border-brand-300 text-ink">CSV</button>
          <button onClick={() => downloadFile("xlsx")} className="text-[11px] font-medium px-2 py-0.5 rounded border border-border bg-white hover:border-brand-300 text-ink">XLSX</button>
          <button onClick={downloadPng} className="text-[11px] font-medium px-2 py-0.5 rounded border border-border bg-white hover:border-brand-300 text-ink">PNG</button>
        </div>
      </div>
      <div ref={ref} className="overflow-x-auto rounded-lg border border-border bg-white">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-bg-soft text-muted text-[10px] uppercase tracking-wide">
              <th className="text-left px-2 py-1.5 font-semibold">{o("colField")}</th>
              <th className="text-left px-2 py-1.5 font-semibold">{o("colLabel")}</th>
              <th className="text-right px-2 py-1.5 font-semibold">{o("colTotal")}</th>
              <th className="text-right px-2 py-1.5 font-semibold">{o("colFilled")}</th>
              <th className="text-right px-2 py-1.5 font-semibold">{o("colMissing")}</th>
              <th className="text-right px-2 py-1.5 font-semibold">{o("colFillRate")}</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((f) => {
              const pct = Math.round(f.fill_rate * 100);
              const pctColor = pct >= 90 ? "text-emerald-700" : pct >= 50 ? "text-amber-700" : "text-red-600";
              return (
                <tr key={f.field} className="border-t border-border">
                  <td className="px-2 py-1 font-mono text-[10px] text-muted">{f.field}</td>
                  <td className="px-2 py-1 text-ink">{fieldLabel(t, f.field, f.label)}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{formatNumber(f.total, locale)}</td>
                  <td className="px-2 py-1 text-right tabular-nums text-emerald-700">{formatNumber(f.filled, locale)}</td>
                  <td className="px-2 py-1 text-right tabular-nums text-muted">{formatNumber(f.missing, locale)}</td>
                  <td className={cn("px-2 py-1 text-right tabular-nums font-semibold", pctColor)}>{fmtPct(f.fill_rate, locale)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-muted mt-1.5">{o("hint")}</p>
    </div>
  );
}

function StatCard({ icon, label, value, hint, accent = "brand" }: {
  icon: React.ReactNode; label: string; value: string; hint?: string;
  accent?: "brand" | "success" | "warning" | "danger";
}) {
  const accentMap = {
    brand: "bg-brand-50 text-brand-700 border-brand-200",
    success: "bg-success-soft text-emerald-700 border-success/30",
    warning: "bg-warning-soft text-amber-700 border-warning/30",
    danger: "bg-danger-soft text-red-700 border-danger/30",
  };
  return (
    <div className={cn("rounded-lg border px-3 py-2.5", accentMap[accent])}>
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide opacity-80">
        {icon}{label}
      </div>
      <div className="text-xl font-bold mt-1 tabular-nums">{value}</div>
      {hint && <div className="text-[10px] opacity-70 mt-0.5">{hint}</div>}
    </div>
  );
}

/** Field → distinguishing color: the "missing" slice in the bar and the left-border color of the box below. */
// Distinguishable MATTE categorical tones from the corporate palette
// (navy/purple/petrol/amber/red families) — consistent with the UI, not a rainbow.
const FIELD_COLORS: Record<string, string> = {
  DI: "#38597f", AB: "#74398c", TI: "#0f766e", AU: "#8e5ba5", PY: "#264667",
  DE: "#a16207", ID: "#c8982f", WC: "#b91c1c", SC: "#d05c5c", C1: "#7593b5",
  EM: "#9a1818", CR: "#5f6f85", TC: "#602a76", LA: "#c43a3a",
  SO: "#28837b", AF: "#4f1964",
};
function fieldColor(f: string): string {
  return FIELD_COLORS[f] ?? "#94A3B8";
}

/** Field code -> localized label (recordDetail.fields.*); if the key is missing it falls back to the backend label. */
function fieldLabel(t: (k: string) => string, code: string, fallback: string): string {
  const k = `recordDetail.fields.${code}`;
  const v = t(k);
  return !v || v === k ? fallback : v;
}

/** Compact field box — the left-border color matches the field color in the bar; shows the % fill health. */
function FieldChip({ field }: {
  field: { field: string; label: string; hint: string; total: number; filled: number; missing: number; fill_rate: number };
}) {
  const t = useT();
  const { locale } = useI18n();
  const pct = Math.round(field.fill_rate * 100);
  const pctColor = pct >= 90 ? "text-emerald-700" : pct >= 50 ? "text-amber-700" : "text-red-600";
  const label = fieldLabel(t, field.field, field.label);
  return (
    <div
      title={`${label} · ${formatNumber(field.filled, locale)} ${t("records.quality.filledShort")} · ${formatNumber(field.missing, locale)} ${t("records.quality.missingShort")}`}
      className="flex items-center gap-1.5 pl-2 pr-2.5 py-1 rounded-md bg-white border border-border border-l-[3px]"
      style={{ borderLeftColor: fieldColor(field.field) }}
    >
      <span className="font-mono text-[10px] text-muted">{field.field}</span>
      <span className="text-[11px] text-ink truncate max-w-[8rem]">{label}</span>
      <span className={cn("ml-1 text-[11px] font-semibold tabular-nums", pctColor)}>{fmtPct(field.fill_rate, locale)}</span>
    </div>
  );
}
