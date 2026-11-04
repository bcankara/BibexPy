"use client";
import { useEffect, useRef, useState } from "react";
import { translateApiError, translateTitle, type JobInfo } from "@/lib/api-client";
import { CheckCircle2, AlertCircle, Loader2, X, Copy } from "lucide-react";
import { Button } from "./Button";
import { useToast } from "./Dialogs";
import { useI18n } from "@/lib/i18n";
import { formatNumber } from "@/lib/i18n/format";
import { cancelJob, isActive, jobErrorText, jobStatusLine, useJob } from "@/lib/job-store";

type Props = {
  jobId: string;
  onComplete?: (job: JobInfo) => void;
  onClose?: () => void;
};

/**
 * Progress card of one job. The job state comes from the shared job store (which owns the
 * EventSource), so the card is re-attach safe: it works for a job that is already running
 * or already finished, and surviving a navigation does not lose the progress.
 */
export function JobProgress({ jobId, onComplete, onClose }: Props) {
  const { t, locale } = useI18n();
  const toast = useToast();
  const job = useJob(jobId);
  const [cancelling, setCancelling] = useState(false);
  // Ref pattern: a change of the onComplete prop must not re-run the effect below.
  const onCompleteRef = useRef(onComplete);
  useEffect(() => { onCompleteRef.current = onComplete; }, [onComplete]);
  // The completion callback fires once per job id (also across StrictMode's double effects).
  const firedRef = useRef<string | null>(null);

  useEffect(() => {
    if (!job || isActive(job) || firedRef.current === job.id) return;
    firedRef.current = job.id;
    onCompleteRef.current?.(job);
  }, [job]);

  if (!job) return <div className="text-muted text-sm">{t("jobs.starting")}</div>;

  const pct = formatNumber(job.progress, locale, { style: "percent", maximumFractionDigits: 0 });
  const isRunning = isActive(job);
  const title = translateTitle(t, job) || t("jobs.starting");
  const statusLine = jobStatusLine(t, job, locale);
  const errorText = jobErrorText(t, job);
  const failed = job.status === "failed";

  async function handleCancel() {
    if (cancelling || !job) return;
    setCancelling(true);
    try { await cancelJob(job.id); }
    catch (e) { toast(translateApiError(t, e), { tone: "danger" }); }
    finally { setCancelling(false); }
  }

  async function copyLog() {
    if (!job) return;
    try {
      await navigator.clipboard.writeText(job.log_tail.join("\n"));
      toast(t("jobs.logCopied"), { tone: "success", duration: 2000 });
    } catch {
      toast(t("errors.generic"), { tone: "danger" });
    }
  }

  return (
    <div className="rounded-xl border border-border bg-bg-card shadow-card p-4 space-y-3">
      <div className="flex items-center gap-2">
        {job.status === "completed" && (
          <span className="w-7 h-7 rounded-full bg-success-soft flex items-center justify-center">
            <CheckCircle2 className="h-4 w-4 text-success" />
          </span>
        )}
        {failed && (
          <span className="w-7 h-7 rounded-full bg-danger-soft flex items-center justify-center">
            <AlertCircle className="h-4 w-4 text-danger" />
          </span>
        )}
        {isRunning && (
          <span className="w-7 h-7 rounded-full bg-[#0c2847] flex items-center justify-center">
            <Loader2 className="h-4 w-4 animate-spin text-cyan-300" />
          </span>
        )}
        <span className="font-semibold text-ink">{title}</span>
        <span className="ml-auto text-sm font-medium text-ink tabular-nums">{pct}</span>
        {isRunning && (
          <Button size="sm" variant="ghost" onClick={handleCancel} disabled={cancelling} title={t("jobs.cancel")}>
            {cancelling ? <Loader2 className="h-4 w-4 animate-spin" /> : <X className="h-4 w-4" />}
          </Button>
        )}
        {!isRunning && onClose && (
          <Button size="sm" variant="ghost" onClick={onClose} title={t("common.close")}>
            <X className="h-4 w-4" />
          </Button>
        )}
      </div>

      <div className="h-2 rounded-full bg-bg-soft overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-brand-500 to-accent transition-all"
          style={{ width: `${Math.round(job.progress * 100)}%` }}
        />
      </div>

      {/* Translated one-line status (status_key) while the job is alive; fallback = last log line. */}
      {isRunning && statusLine && (
        <p className="text-xs text-muted truncate" title={statusLine}>{statusLine}</p>
      )}

      {errorText && (
        <div className="text-sm text-red-700 bg-danger-soft border border-danger/30 rounded-lg p-2 whitespace-pre-wrap">
          {errorText}
        </div>
      )}

      {job.log_tail.length > 0 && (
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-muted">
              {t("jobs.logHeader")}
            </span>
            <button
              type="button"
              onClick={copyLog}
              title={t("jobs.copyLog")}
              className="ml-auto inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-muted hover:bg-bg-soft hover:text-ink"
            >
              <Copy className="h-3 w-3" /> {t("jobs.copyLog")}
            </button>
          </div>
          <pre className="text-xs text-muted bg-bg-soft rounded-lg p-2.5 max-h-40 overflow-y-auto whitespace-pre-wrap font-mono">
            {job.log_tail.join("\n")}
          </pre>
        </div>
      )}

      {job.status === "completed" && job.result != null && (
        <details className="text-xs">
          <summary className="text-muted cursor-pointer hover:text-ink">{t("jobs.result")}</summary>
          <pre className="mt-2 bg-bg-soft rounded-lg p-2 max-h-60 overflow-auto font-mono">
            {JSON.stringify(job.result, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
