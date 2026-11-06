"use client";
import { useEffect, useRef, useState } from "react";
import { api, type UidMigrationError, type UidMigrationStatus } from "@/lib/api-client";
import { useI18n } from "@/lib/i18n";
import { formatNumber } from "@/lib/i18n/format";
import { BlockingOverlay } from "./BlockingOverlay";

/**
 * One-time record migration window (global: mounted by AppShell, so it covers every page,
 * including the home page).
 *
 * After an update the backend converts every saved analysis to the new sequential record keys
 * in the background (GET /api/system/uid-migration reports the progress). While that runs the
 * whole app is blocked by an overlay with the progress. When it ends:
 *   - success: a short "done" notice, then the page reloads once so nothing keeps showing data
 *     that was read while the keys were being rewritten;
 *   - some analyses could not be converted: the overlay lists them and lets the user continue
 *     (those analyses keep working with their old keys).
 * If no migration is needed the backend reports "done" at once and nothing is shown.
 */

const POLL_RUNNING_MS = 1_000;
const POLL_IDLE_MS = 500;
/** "idle" right after start-up is normal for a moment; stop waiting for the first run after this. */
const MAX_IDLE_POLLS = 20;
/** The backend may still be starting (or restarting): keep trying for a while, silently. */
const MAX_FAILED_POLLS = 60;
const POLL_FAILED_MS = 2_000;
const SUCCESS_NOTICE_MS = 1_800;
/** Acknowledged error reports (session only), so a reload does not show the same list again. */
const ACK_KEY = "bibexpy.uidMigration.errorsAck";

type Phase =
  | { kind: "hidden" }
  | { kind: "running"; status: UidMigrationStatus }
  | { kind: "success" }
  | { kind: "errors"; items: string[] };

/** One readable line per failed analysis (the status may carry plain strings or objects). */
function describeErrors(status: UidMigrationStatus): string[] {
  const out: string[] = [];
  for (const e of status.errors ?? []) {
    if (typeof e === "string") {
      if (e.trim()) out.push(e.trim());
      continue;
    }
    const it = e as UidMigrationError;
    const where = [it.project_name ?? it.project ?? it.project_id, it.analysis_name ?? it.analysis ?? it.analysis_id]
      .filter((x): x is string => !!x && x.trim() !== "")
      .join(" / ");
    const why = (it.message ?? it.error ?? "").trim();
    const line = [where, why].filter(Boolean).join(": ");
    if (line) out.push(line);
  }
  // The single summary text is English backend output: only use it when no per-analysis line exists.
  if (out.length === 0 && status.error && status.error.trim()) out.push(status.error.trim());
  return out;
}

function signature(items: string[]): string {
  return items.join("\n");
}

function readAck(): string | null {
  try { return sessionStorage.getItem(ACK_KEY); } catch { return null; }
}
function writeAck(sig: string): void {
  try { sessionStorage.setItem(ACK_KEY, sig); } catch { /* storage may be unavailable */ }
}

export function MigrationGate() {
  const { t, locale } = useI18n();
  const [phase, setPhase] = useState<Phase>({ kind: "hidden" });
  // Set once an unfinished migration was seen, so "done" after that is announced.
  const sawRunning = useRef(false);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let idlePolls = 0;
    let failedPolls = 0;

    const later = (ms: number) => {
      if (alive) timer = setTimeout(poll, ms);
    };

    async function poll() {
      let status: UidMigrationStatus;
      try {
        status = await api.uidMigrationStatus();
      } catch (e) {
        if (!alive) return;
        // An older backend without the endpoint has nothing to migrate.
        if (e instanceof Error && /^404\b/.test(e.message)) return;
        failedPolls += 1;
        if (failedPolls <= MAX_FAILED_POLLS) {
          later(POLL_FAILED_MS);
        } else {
          // The backend is gone: an overlay that can never end would trap the user.
          setPhase({ kind: "hidden" });
        }
        return;
      }
      if (!alive) return;
      failedPolls = 0;

      if (status.state === "running") {
        sawRunning.current = true;
        setPhase({ kind: "running", status });
        later(POLL_RUNNING_MS);
        return;
      }
      if (status.state === "idle") {
        // The background job has not started yet (or there is nothing to do).
        idlePolls += 1;
        if (sawRunning.current) setPhase({ kind: "running", status });
        if (idlePolls <= MAX_IDLE_POLLS) later(POLL_IDLE_MS);
        return;
      }

      // done / error
      const items = describeErrors(status);
      if (items.length === 0 && status.state === "error") items.push(t("errors.generic"));
      if (items.length > 0) {
        const sig = signature(items);
        if (sawRunning.current || readAck() !== sig) setPhase({ kind: "errors", items });
        return;
      }
      if (sawRunning.current) {
        setPhase({ kind: "success" });
        timer = setTimeout(() => {
          // Data fetched while the keys were being rewritten may be stale: load everything again.
          try { window.location.reload(); } catch { /* ignore */ }
        }, SUCCESS_NOTICE_MS);
      } else {
        setPhase({ kind: "hidden" });
      }
    }

    void poll();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, []);

  if (phase.kind === "hidden") return null;

  if (phase.kind === "success") {
    return (
      <BlockingOverlay
        tone="success"
        title={t("migration.doneTitle")}
        note={t("migration.doneNote")}
      />
    );
  }

  if (phase.kind === "errors") {
    return (
      <BlockingOverlay
        tone="warning"
        title={t("migration.errorTitle")}
        note={t("migration.errorNote")}
        details={phase.items}
        continueLabel={t("migration.continue")}
        onContinue={() => {
          const sig = signature(phase.items);
          writeAck(sig);
          // Reload so every page re-reads its data; without working storage the acknowledgement
          // would not survive the reload and the same window would come back, so just close it.
          if (readAck() === sig) {
            try { window.location.reload(); return; } catch { /* fall through */ }
          }
          setPhase({ kind: "hidden" });
        }}
      />
    );
  }

  const s = phase.status;
  const total = Math.max(0, s.projects_total ?? 0);
  const done = Math.max(0, Math.min(total, s.projects_done ?? 0));
  const progress = total > 0 ? done / total : null;
  const lines: string[] = [];
  if (total > 0) {
    lines.push(t("migration.projects", { done: formatNumber(done, locale), total: formatNumber(total, locale) }));
  }
  if (s.current_project_name) lines.push(t("migration.current", { project: s.current_project_name }));
  return (
    <BlockingOverlay
      title={t("migration.title")}
      note={t("migration.note")}
      progress={progress}
      status={lines.length > 0 ? lines.join(" · ") : t("migration.preparing")}
    />
  );
}
