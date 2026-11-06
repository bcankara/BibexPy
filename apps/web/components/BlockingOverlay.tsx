"use client";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, CheckCircle2, Loader2, ShieldAlert } from "lucide-react";
import { translateApiError, translateTitle } from "@/lib/api-client";
import { useI18n } from "@/lib/i18n";
import { formatNumber } from "@/lib/i18n/format";
import { useProjectId } from "@/lib/use-project-id";
import { beginProjectUpload, cancelJob, jobStatusLine, useProjectBusy } from "@/lib/job-store";
import { Button } from "./Button";

/**
 * Full-viewport "nothing else can be clicked" overlay.
 *
 * Used for the BLOCKING operations: a file upload in flight (browser-side transfer), a merge
 * job of the current project (which includes the automatic preparation step) and the one-time
 * record migration after an update (`MigrationGate`). Longer background jobs (enrichment,
 * disambiguation) do NOT use it; their data-changing controls are disabled instead
 * (`useProjectBusy`).
 *
 * How the block works:
 *   - it is portalled to <body> with a z-index above the ribbon, drawers and dialogs and
 *     captures the pointer;
 *   - every other child of <body> gets the `inert` attribute while any overlay is mounted
 *     (ref-counted, so two overlays at once restore correctly), which also removes the page
 *     from the keyboard tab order and from assistive technology;
 *   - focus moves into the overlay and Tab stays inside it (fallback for browsers without
 *     `inert`).
 */

/**
 * Extra classes for a disabled control that explains itself with a `title`: `Button` sets
 * `disabled:pointer-events-none`, which would also suppress the tooltip with the reason.
 */
export const LOCKED_CLASS = "disabled:pointer-events-auto disabled:cursor-not-allowed";

const OVERLAY_ATTR = "data-blocking-overlay";
const inertCounts = new Map<Element, number>();

function lockPage(): () => void {
  const locked: Element[] = [];
  for (const el of Array.from(document.body.children)) {
    if (el.hasAttribute(OVERLAY_ATTR)) continue;
    const n = inertCounts.get(el) ?? 0;
    if (n === 0) el.setAttribute("inert", "");
    inertCounts.set(el, n + 1);
    locked.push(el);
  }
  return () => {
    for (const el of locked) {
      const n = (inertCounts.get(el) ?? 1) - 1;
      if (n <= 0) {
        inertCounts.delete(el);
        el.removeAttribute("inert");
      } else {
        inertCounts.set(el, n);
      }
    }
  };
}

/**
 * Uploads in flight (module level on purpose): the browser warning must outlive the component
 * that started the transfer. If the user leaves the page through the browser's back button the
 * upload component unmounts, but the transfer keeps running and closing the tab would still
 * abort it, so the guard cannot be tied to a component effect. The upload itself is also
 * registered in the job store (`beginProjectUpload`), which is what the project layout renders
 * the overlay from.
 */
let uploadsInFlight = 0;

function onBeforeUnload(e: BeforeUnloadEvent) {
  e.preventDefault();
  // Legacy browsers need a non-empty returnValue to show the prompt.
  e.returnValue = "";
}

/**
 * Register one upload of `count` files in flight: warn before the tab is closed or reloaded
 * and publish it to the job store, so the project layout shows the blocking overlay on every
 * page until it ends. Returns the function that ends it (call it in `finally`; calling it
 * twice is harmless). Pass the error to it when the transfer failed.
 */
export function beginUpload(projectId: string, count: number): (error?: unknown) => void {
  uploadsInFlight += 1;
  if (uploadsInFlight === 1) window.addEventListener("beforeunload", onBeforeUnload);
  const endInStore = beginProjectUpload(projectId, count);
  let ended = false;
  return (error?: unknown) => {
    if (ended) return;
    ended = true;
    uploadsInFlight -= 1;
    if (uploadsInFlight <= 0) {
      uploadsInFlight = 0;
      window.removeEventListener("beforeunload", onBeforeUnload);
    }
    endInStore(error);
  };
}

export type BlockingOverlayProps = {
  title: string;
  /** Short explanation line under the title. */
  note?: string;
  /** 0..1, or null/undefined for an indeterminate bar. */
  progress?: number | null;
  /** One-line status text. */
  status?: string | null;
  /** Presence shows the cancel button (with an in-overlay confirmation step). */
  onCancel?: () => Promise<void>;
  cancelLabel?: string;
  /**
   * Icon in the header. "spinner" (default) while work is in progress; "success" / "warning"
   * for a finished state (no progress bar is drawn for them).
   */
  tone?: "spinner" | "success" | "warning";
  /** Extra lines under the status (e.g. which items failed). */
  details?: string[];
  /** Presence shows a primary button that dismisses the overlay (used by finished states). */
  onContinue?: () => void;
  continueLabel?: string;
};

export function BlockingOverlay({
  title, note, progress, status, onCancel, cancelLabel, tone = "spinner", details, onContinue, continueLabel,
}: BlockingOverlayProps) {
  const { t, locale } = useI18n();
  const [mounted, setMounted] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (!mounted) return;
    const previous = document.activeElement as HTMLElement | null;
    const unlock = lockPage();
    panelRef.current?.focus();
    return () => {
      unlock();
      try { previous?.focus?.(); } catch { /* the element may be gone */ }
    };
  }, [mounted]);

  if (!mounted) return null;

  function onKeyDown(e: React.KeyboardEvent) {
    // Keep Escape (and every other key) away from the page's own document-level shortcuts
    // (drawers close on Escape); the page behind the overlay must not react to the keyboard.
    if (e.key !== "Tab") {
      e.stopPropagation();
      return;
    }
    const panel = panelRef.current;
    if (!panel) return;
    const focusable = Array.from(
      panel.querySelectorAll<HTMLElement>("button:not([disabled]), [href], input, select, textarea"),
    );
    e.preventDefault();
    if (focusable.length === 0) { panel.focus(); return; }
    const i = focusable.indexOf(document.activeElement as HTMLElement);
    const next = e.shiftKey ? (i <= 0 ? focusable.length - 1 : i - 1) : (i + 1) % focusable.length;
    focusable[next].focus();
  }

  async function doCancel() {
    if (!onCancel || cancelling) return;
    setCancelling(true);
    setCancelError(null);
    try {
      await onCancel();
      setConfirming(false);
    } catch (e) {
      setCancelError(translateApiError(t, e));
    } finally {
      setCancelling(false);
    }
  }

  const hasProgress = typeof progress === "number" && Number.isFinite(progress);
  const pct = hasProgress ? Math.max(0, Math.min(1, progress as number)) : 0;

  return createPortal(
    <div
      {...{ [OVERLAY_ATTR]: "" }}
      className="fixed inset-0 z-[110] flex items-center justify-center bg-ink/55 p-4 backdrop-blur-sm"
      // Swallow every pointer interaction that reaches the backdrop.
      onClick={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}
      // A file dropped outside a drop zone would make the browser open it and replace the page
      // (aborting a running upload), so refuse drags over the overlay.
      onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "none"; }}
      onDrop={(e) => e.preventDefault()}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-busy={tone === "spinner"}
        aria-labelledby="blocking-overlay-title"
        tabIndex={-1}
        onKeyDown={onKeyDown}
        className="w-full max-w-md space-y-4 rounded-2xl bg-white p-6 shadow-2xl outline-none"
      >
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full bg-[#0c2847]">
            {tone === "success" ? (
              <CheckCircle2 className="h-5 w-5 text-emerald-300" />
            ) : tone === "warning" ? (
              <AlertTriangle className="h-5 w-5 text-amber-300" />
            ) : (
              <Loader2 className="h-5 w-5 animate-spin text-cyan-200" />
            )}
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="blocking-overlay-title" className="text-base font-semibold text-ink">{title}</h2>
            {note && <p className="mt-0.5 text-xs text-muted">{note}</p>}
          </div>
          {hasProgress && tone === "spinner" && (
            <span className="flex-shrink-0 text-sm font-bold tabular-nums text-cyan-700">
              {formatNumber(pct, locale, { style: "percent", maximumFractionDigits: 0 })}
            </span>
          )}
        </div>

        {tone === "spinner" && (
          <div
            className="h-2 overflow-hidden rounded-full bg-cyan-100"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={hasProgress ? Math.round(pct * 100) : undefined}
          >
            <div
              className={hasProgress ? "h-full bg-cyan-500 transition-all" : "h-full w-full animate-pulse bg-cyan-400"}
              style={hasProgress ? { width: `${Math.round(pct * 100)}%` } : undefined}
            />
          </div>
        )}

        {status && <p className="truncate text-xs text-muted" title={status} aria-live="polite">{status}</p>}

        {details && details.length > 0 && (
          <ul className="max-h-40 space-y-1 overflow-y-auto rounded-lg border border-border bg-bg-soft/50 px-3 py-2 text-xs text-ink">
            {details.map((d, i) => (
              <li key={i} className="break-words">{d}</li>
            ))}
          </ul>
        )}

        {onContinue && (
          <div className="flex justify-end">
            <Button size="sm" onClick={onContinue}>{continueLabel ?? t("common.close")}</Button>
          </div>
        )}

        {cancelError && (
          <div className="rounded-lg border border-danger/30 bg-danger-soft px-3 py-2 text-xs text-red-700">
            {cancelError}
          </div>
        )}

        {onCancel && !confirming && (
          <div className="flex justify-end">
            <Button variant="secondary" size="sm" onClick={() => setConfirming(true)}>
              {cancelLabel ?? t("lock.cancelOperation")}
            </Button>
          </div>
        )}
        {onCancel && confirming && (
          <div className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-3">
            <p className="flex items-start gap-2 text-xs text-amber-900">
              <ShieldAlert className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
              {t("lock.cancelConfirm")}
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setConfirming(false)} disabled={cancelling}>
                {t("lock.keepRunning")}
              </Button>
              <Button variant="danger" size="sm" onClick={doCancel} disabled={cancelling}>
                {cancelling && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {t("lock.confirmCancel")}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

/**
 * Blocking overlay of the current project, rendered once by the project layout so it covers
 * every project page and survives SPA navigation / the browser's back button:
 *   - a MERGE job (also after a reload or navigation: the job store re-reads active jobs);
 *   - a merge that is being STARTED (the start request has not returned yet);
 *   - a file UPLOAD in flight (state lives in the job store, not in the upload section).
 * It disappears as soon as the operation ends; the finish toast is owned by the jobs banner.
 */
export function ProjectBlockingOverlay() {
  const projectId = useProjectId();
  const { t, locale } = useI18n();
  const { blocking, mergeStarting, uploading } = useProjectBusy(projectId);
  if (blocking) {
    const status = jobStatusLine(t, blocking, locale)
      ?? (blocking.status === "queued" ? t("jobs.queued") : t("jobs.running"));
    return (
      <BlockingOverlay
        title={translateTitle(t, blocking) || t("jobs.starting")}
        note={t("lock.mergeNote")}
        progress={blocking.progress}
        status={status}
        onCancel={() => cancelJob(blocking.id)}
        cancelLabel={t("lock.cancelMerge")}
      />
    );
  }
  if (mergeStarting) {
    return <BlockingOverlay title={t("lock.mergeStarting")} note={t("lock.mergeNote")} />;
  }
  if (uploading > 0) {
    return (
      <BlockingOverlay
        title={t("lock.uploading", { n: formatNumber(uploading, locale) })}
        note={t("lock.uploadNote")}
      />
    );
  }
  return null;
}
