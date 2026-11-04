"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Loader2, X, ArrowRight, Server } from "lucide-react";
import { api, translateApiError, translateTitle } from "@/lib/api-client";
import { useI18n } from "@/lib/i18n";
import { formatNumber } from "@/lib/i18n/format";
import { useProjectId } from "@/lib/use-project-id";
import {
  cancelJob, isOnOwnerPage, jobErrorText, jobOwnerHref, jobStatusLine, setToastSink,
  useProjectJobs, type JobFinishedEvent, type StoreJob,
} from "@/lib/job-store";
import { useConfirm, useToast } from "./Dialogs";

/**
 * Persistent "job running" strip for every project page (rendered from the project layout).
 *
 * Jobs run on the server and survive navigation; this strip makes them visible from any
 * page of the project, links to the page that owns the job and lets the user cancel it.
 * It also owns the terminal-state toasts: the root DialogProvider's toast outlives page
 * changes, and the store hands this component every finished-job event.
 */
export function ProjectJobsBanner() {
  const projectId = useProjectId();
  const { t, locale } = useI18n();
  const toast = useToast();
  const { active } = useProjectJobs(projectId);

  // Terminal-state toasts (completed / failed / lost). Events that happened while no banner
  // was mounted (e.g. on the Settings page) are delivered when this registers.
  useEffect(() => {
    setToastSink((e: JobFinishedEvent) => {
      const show = (suffix: string) => {
        const title = jobTitle(t, e.job);
        if (e.outcome === "completed") {
          toast(t("jobs.toast.completed", { title }) + suffix, { tone: "success" });
        } else if (e.outcome === "lost") {
          toast(t("jobs.toast.lost", { title }) + suffix, { tone: "warning", duration: 9000 });
        } else if (e.outcome === "failed") {
          toast(t("jobs.toast.failed", { title, error: jobErrorText(t, e.job) ?? t("errors.generic") }) + suffix, {
            tone: "danger",
          });
        }
      };
      // A job of ANOTHER project (its stream stays open after the user moves on) must say
      // which project it belongs to, otherwise it reads as this project's job.
      const owner = e.job.project_id;
      if (owner && owner !== projectId) {
        void projectName(owner).then((name) => show(" " + t("jobs.toast.inProject", { project: name })));
      } else {
        show("");
      }
    });
    return () => setToastSink(null);
  }, [t, toast, projectId]);

  if (active.length === 0) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="border-b border-cyan-200 bg-cyan-50 text-ink"
    >
      <div className="mx-auto max-w-6xl space-y-2 px-6 py-2.5">
        {active.map((job) => (
          <BannerRow key={job.id} job={job} projectId={projectId} locale={locale} />
        ))}
        <p className="flex items-center gap-1.5 text-[11px] text-muted">
          <Server className="h-3 w-3 flex-shrink-0" />
          {t("jobs.banner.note")}
        </p>
      </div>
    </div>
  );
}

const projectNames = new Map<string, string>();

/** Project display name for cross-project toasts (cached; falls back to the id). */
async function projectName(id: string): Promise<string> {
  const cached = projectNames.get(id);
  if (cached) return cached;
  try {
    const p = await api.getProject(id);
    projectNames.set(id, p.name);
    return p.name;
  } catch {
    return id;
  }
}

function jobTitle(t: (k: string, v?: Record<string, string | number>) => string, job: StoreJob): string {
  const title = translateTitle(t, job);
  return title || t("jobs.starting");
}

function BannerRow({ job, projectId, locale }: {
  job: StoreJob; projectId: string; locale: "en" | "tr";
}) {
  const { t } = useI18n();
  const confirm = useConfirm();
  const toast = useToast();
  const pathname = usePathname();
  const [cancelling, setCancelling] = useState(false);

  const href = jobOwnerHref(job, projectId);
  const showOpen = !!href && !isOnOwnerPage(pathname, href);
  const pct = formatNumber(job.progress, locale, { style: "percent", maximumFractionDigits: 0 });
  const status = jobStatusLine(t, job, locale) ?? (job.status === "queued" ? t("jobs.queued") : t("jobs.running"));
  const title = jobTitle(t, job);

  async function handleCancel() {
    if (cancelling) return;
    const ok = await confirm({
      title: t("jobs.banner.cancelConfirmTitle"),
      message: t("jobs.banner.cancelConfirm", { title }),
      confirmLabel: t("jobs.banner.cancel"),
      tone: "danger",
    });
    if (!ok) return;
    setCancelling(true);
    try {
      await cancelJob(job.id);
    } catch (e) {
      toast(translateApiError(t, e), { tone: "danger" });
    } finally {
      setCancelling(false);
    }
  }

  return (
    <div className="flex items-center gap-3">
      <Loader2 className="h-4 w-4 flex-shrink-0 animate-spin text-cyan-600" />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="truncate text-sm font-semibold">{title}</span>
          <span className="flex-shrink-0 text-xs font-bold tabular-nums text-cyan-700">{pct}</span>
        </div>
        <div className="truncate text-xs text-muted" title={status}>{status}</div>
        <div className="mt-1 h-1 overflow-hidden rounded-full bg-cyan-100">
          <div
            className="h-full bg-cyan-500 transition-all"
            style={{ width: `${Math.round(job.progress * 100)}%` }}
          />
        </div>
      </div>
      {showOpen && href && (
        <Link
          href={href}
          className="flex flex-shrink-0 items-center gap-1 rounded-md border border-cyan-300 bg-white px-2.5 py-1 text-xs font-semibold text-cyan-700 transition hover:bg-cyan-100"
        >
          {t("jobs.banner.open")} <ArrowRight className="h-3 w-3" />
        </Link>
      )}
      <button
        onClick={handleCancel}
        disabled={cancelling}
        className="flex flex-shrink-0 items-center gap-1 rounded-md border border-border bg-white px-2.5 py-1 text-xs font-semibold text-muted transition hover:text-ink disabled:opacity-60"
      >
        {cancelling ? <Loader2 className="h-3 w-3 animate-spin" /> : <X className="h-3 w-3" />}
        {t("jobs.banner.cancel")}
      </button>
    </div>
  );
}
