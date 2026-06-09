"use client";
import { useEffect, useRef, useState } from "react";
import { api, translateTitle, type JobInfo } from "@/lib/api-client";
import { CheckCircle2, AlertCircle, Loader2, X } from "lucide-react";
import { Button } from "./Button";
import { useT } from "@/lib/i18n";

type Props = {
  jobId: string;
  onComplete?: (job: JobInfo) => void;
  onClose?: () => void;
};

export function JobProgress({ jobId, onComplete, onClose }: Props) {
  const t = useT();
  const [job, setJob] = useState<JobInfo | null>(null);
  // Ref pattern: onComplete prop değişimi useEffect'i tetiklemez, böylece
  // EventSource her render'da kapanıp açılmaz ve "completed" event'i kaçırılmaz.
  const onCompleteRef = useRef(onComplete);
  useEffect(() => { onCompleteRef.current = onComplete; }, [onComplete]);
  // "completed" callback'inin tek seferde tetiklenmesini garanti et
  const firedRef = useRef(false);

  useEffect(() => {
    firedRef.current = false;
    const es = new EventSource(api.jobStreamUrl(jobId));
    es.addEventListener("update", (e) => {
      try {
        const data = JSON.parse((e as MessageEvent).data) as JobInfo;
        setJob(data);
        if (["completed", "failed", "cancelled"].includes(data.status) && !firedRef.current) {
          firedRef.current = true;
          es.close();
          onCompleteRef.current?.(data);
        }
      } catch {}
    });
    es.addEventListener("done", () => { es.close(); });
    es.onerror = () => { es.close(); };
    return () => { es.close(); };
  }, [jobId]);

  if (!job) return <div className="text-muted text-sm">{t("jobs.starting")}</div>;

  const pct = Math.round(job.progress * 100);
  const isRunning = job.status === "queued" || job.status === "running";

  return (
    <div className="rounded-xl border border-border bg-bg-card shadow-card p-4 space-y-3">
      <div className="flex items-center gap-2">
        {job.status === "completed" && (
          <span className="w-7 h-7 rounded-full bg-success-soft flex items-center justify-center">
            <CheckCircle2 className="h-4 w-4 text-success" />
          </span>
        )}
        {job.status === "failed" && (
          <span className="w-7 h-7 rounded-full bg-danger-soft flex items-center justify-center">
            <AlertCircle className="h-4 w-4 text-danger" />
          </span>
        )}
        {isRunning && (
          <span className="w-7 h-7 rounded-full bg-[#0c2847] flex items-center justify-center">
            <Loader2 className="h-4 w-4 animate-spin text-cyan-300" />
          </span>
        )}
        <span className="font-semibold text-ink">{translateTitle(t, job)}</span>
        <span className="ml-auto text-sm font-medium text-ink tabular-nums">{pct}%</span>
        {isRunning && (
          <Button size="sm" variant="ghost" onClick={() => api.cancelJob(job.id)} title={t("jobs.cancel")}>
            <X className="h-4 w-4" />
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
          style={{ width: `${pct}%` }}
        />
      </div>

      {job.error && (
        <div className="text-sm text-red-700 bg-danger-soft border border-danger/30 rounded-lg p-2 whitespace-pre-wrap">
          {job.error}
        </div>
      )}

      {job.log_tail.length > 0 && (
        <pre className="text-xs text-muted bg-bg-soft rounded-lg p-2.5 max-h-40 overflow-y-auto whitespace-pre-wrap font-mono">
          {job.log_tail.join("\n")}
        </pre>
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
