"use client";
import { useEffect, useRef, useSyncExternalStore } from "react";
import { usePathname } from "next/navigation";
import { api, translateApiError, type JobInfo } from "./api-client";
import type { Locale } from "./i18n";
import { formatNumber } from "./i18n/format";

/**
 * Module-level store of background jobs, shared by every page.
 *
 * Why: a job runs on the SERVER and outlives the page that started it, but the old
 * per-component state (useState + EventSource) died on every navigation, so the UI
 * "forgot" a running job and let the user start a second one (HTTP 409). Here:
 *   - jobs live in module state, not in components;
 *   - the store (not components) owns ONE EventSource per active job and reconnects with
 *     backoff after checking the job still exists on the server;
 *   - `useProjectJobs(projectId)` hydrates from GET /jobs?project_id=&active=true on first
 *     use, on window focus / tab visibility, on route change, on the `bibexpy:status`
 *     event and by polling (~5 s while a job is active, ~20 s idle);
 *   - terminal transitions are announced once: finish listeners, a toast event for the
 *     banner and the `bibexpy:status` window event (pipeline gating refresh).
 * Everything is idempotent, so React StrictMode's double effects are harmless.
 */

/** A tracked job. `lost` = the server no longer knows it (restart); it is shown as failed. */
export type StoreJob = JobInfo & { lost?: boolean };

export type ProjectJobs = {
  /** Jobs known in this session (active first-seen + recently finished), newest first. */
  jobs: StoreJob[];
  /** Only queued/running ones. */
  active: StoreJob[];
  /** True after the first server lookup finished (success or failure). */
  ready: boolean;
};

export type JobOutcome = "completed" | "failed" | "cancelled" | "lost";
export type JobFinishedEvent = { job: StoreJob; outcome: JobOutcome };

const POLL_ACTIVE_MS = 5_000;
const POLL_IDLE_MS = 20_000;
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 15_000;
const RECONNECT_MAX_ATTEMPTS = 20;
/** Finished jobs kept per project in memory. */
const KEEP_FINISHED = 20;

const EMPTY: ProjectJobs = Object.freeze({ jobs: [], active: [], ready: false }) as ProjectJobs;

export function isActive(job: { status: string }): boolean {
  return job.status === "queued" || job.status === "running";
}

function isNotFound(e: unknown): boolean {
  return e instanceof Error && /^404\b/.test(e.message);
}

/** HTTP 409 job_already_running / dataset_busy_job_running thrown by a start/mutation call. */
export function isBusyError(e: unknown): boolean {
  return e instanceof Error && /job_already_running|dataset_busy_job_running|dataset_busy_request_running/.test(e.message);
}

/* ───────────────────────── state ───────────────────────── */

const jobs = new Map<string, StoreJob>();
const readyProjects = new Set<string>();
const projectVersion = new Map<string, number>();
const snapshots = new Map<string, { v: number; value: ProjectJobs }>();
const subscribers = new Set<() => void>();
const finishListeners = new Set<(e: JobFinishedEvent) => void>();
/** Job ids whose end was already announced (listeners / toast / status event fire once). */
const announced = new Set<string>();

function emit(projectId: string | null | undefined): void {
  if (projectId) projectVersion.set(projectId, (projectVersion.get(projectId) ?? 0) + 1);
  for (const cb of Array.from(subscribers)) {
    try { cb(); } catch { /* a faulty subscriber must not break the store */ }
  }
}

function subscribe(cb: () => void): () => void {
  subscribers.add(cb);
  return () => { subscribers.delete(cb); };
}

function projectSnapshot(projectId: string): ProjectJobs {
  if (!projectId || projectId === "_") return EMPTY;
  const v = projectVersion.get(projectId) ?? 0;
  const cached = snapshots.get(projectId);
  if (cached && cached.v === v) return cached.value;
  const list = Array.from(jobs.values())
    .filter((j) => j.project_id === projectId)
    .sort((a, b) => b.created_at - a.created_at);
  const value: ProjectJobs = {
    jobs: list,
    active: list.filter(isActive),
    ready: readyProjects.has(projectId),
  };
  snapshots.set(projectId, { v, value });
  return value;
}

/** Non-hook read access (event handlers). */
export function getProjectJobs(projectId: string): ProjectJobs {
  return projectSnapshot(projectId);
}

/* ───────────────────────── finish announcements ───────────────────────── */

type ToastSink = (e: JobFinishedEvent) => void;
let toastSink: ToastSink | null = null;
const toastBacklog: JobFinishedEvent[] = [];
const TOAST_BACKLOG_MAX = 10;

/**
 * Register the function that shows terminal-state toasts (the project banner does this
 * with the root DialogProvider's toast). Events that happened while nobody was registered
 * (e.g. the user was on Settings) are delivered as soon as a sink appears.
 */
export function setToastSink(sink: ToastSink | null): void {
  toastSink = sink;
  if (sink) {
    while (toastBacklog.length) {
      const e = toastBacklog.shift()!;
      try { sink(e); } catch { /* ignore */ }
    }
  }
}

/** Subscribe to job end events (completed/failed/cancelled/lost). Returns an unsubscribe fn. */
export function onJobFinished(cb: (e: JobFinishedEvent) => void): () => void {
  finishListeners.add(cb);
  return () => { finishListeners.delete(cb); };
}

/** Hook form of `onJobFinished`; the latest callback is always used (no resubscribing). */
export function useJobFinished(cb: (e: JobFinishedEvent) => void): void {
  const ref = useRef(cb);
  useEffect(() => { ref.current = cb; }, [cb]);
  useEffect(() => onJobFinished((e) => ref.current(e)), []);
}

function announceFinished(job: StoreJob, outcome: JobOutcome): void {
  if (announced.has(job.id)) return;
  announced.add(job.id);
  const ev: JobFinishedEvent = { job, outcome };
  for (const l of Array.from(finishListeners)) {
    try { l(ev); } catch { /* ignore */ }
  }
  // A user-initiated cancel does not need a toast.
  if (outcome !== "cancelled") {
    if (toastSink) {
      try { toastSink(ev); } catch { /* ignore */ }
    } else {
      toastBacklog.push(ev);
      if (toastBacklog.length > TOAST_BACKLOG_MAX) toastBacklog.shift();
    }
  }
  try { window.dispatchEvent(new Event("bibexpy:status")); } catch { /* ignore */ }
}

/* ───────────────────────── upsert / lost ───────────────────────── */

function pruneFinished(projectId: string): void {
  const finished = Array.from(jobs.values())
    .filter((j) => j.project_id === projectId && !isActive(j))
    .sort((a, b) => b.created_at - a.created_at);
  for (const j of finished.slice(KEEP_FINISHED)) {
    jobs.delete(j.id);
    announced.delete(j.id);
  }
}

function upsert(info: JobInfo): StoreJob {
  const prev = jobs.get(info.id);
  // A terminal job never goes back to active (late or reordered frames).
  if (prev && !isActive(prev) && isActive(info)) return prev;
  const next: StoreJob = { ...info };
  jobs.set(next.id, next);
  if (!isActive(next)) {
    pruneFinished(next.project_id);
    // Announce only transitions we actually witnessed (active -> terminal).
    if (prev && isActive(prev)) announceFinished(next, next.status as JobOutcome);
  }
  emit(next.project_id);
  return next;
}

function markLost(jobId: string): void {
  const prev = jobs.get(jobId);
  if (!prev || !isActive(prev)) return;
  const lost: StoreJob = {
    ...prev, status: "failed", lost: true, error: null, error_code: "job_lost",
    finished_at: prev.finished_at ?? Date.now() / 1000,
  };
  jobs.set(jobId, lost);
  emit(lost.project_id);
  announceFinished(lost, "lost");
}

function placeholder(projectId: string, jobId: string): StoreJob {
  return {
    id: jobId, project_id: projectId, kind: "", title: "", title_key: null, title_params: null,
    status: "queued", progress: 0, log_tail: [], status_key: null, status_params: null,
    result: null, error: null, error_code: null, started_at: null, finished_at: null,
    created_at: Date.now() / 1000,
  };
}

/* ───────────────────────── streams (one EventSource per active job) ───────────────────────── */

type StreamState = { es: EventSource | null; attempts: number; timer: ReturnType<typeof setTimeout> | null };
const streams = new Map<string, StreamState>();

function closeStream(jobId: string): void {
  const st = streams.get(jobId);
  if (!st) return;
  if (st.es) { try { st.es.close(); } catch { /* ignore */ } }
  if (st.timer) clearTimeout(st.timer);
  streams.delete(jobId);
}

function scheduleReconnect(jobId: string): void {
  const st = streams.get(jobId);
  if (!st || st.timer) return;
  st.attempts += 1;
  if (st.attempts > RECONNECT_MAX_ATTEMPTS) {
    // Give up for now; the next hydration (focus / poll) re-opens a stream if still needed.
    streams.delete(jobId);
    return;
  }
  const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** (st.attempts - 1));
  st.timer = setTimeout(async () => {
    st.timer = null;
    try {
      // The stream broke: find out whether the job is still alive before reconnecting.
      const cur = upsert(await api.getJob(jobId));
      if (isActive(cur)) openStream(jobId);
      else closeStream(jobId);
    } catch (e) {
      if (isNotFound(e)) { markLost(jobId); closeStream(jobId); }
      else scheduleReconnect(jobId);            // server unreachable: back off and retry
    }
  }, delay);
}

function openStream(jobId: string): void {
  if (typeof window === "undefined" || typeof EventSource === "undefined") return;
  const existing = streams.get(jobId);
  if (existing && (existing.es || existing.timer)) return;
  const st: StreamState = existing ?? { es: null, attempts: 0, timer: null };
  streams.set(jobId, st);
  let es: EventSource;
  try {
    es = new EventSource(api.jobStreamUrl(jobId));
  } catch {
    scheduleReconnect(jobId);
    return;
  }
  st.es = es;
  es.addEventListener("update", (ev) => {
    try {
      const info = JSON.parse((ev as MessageEvent).data) as JobInfo;
      st.attempts = 0;
      if (!isActive(upsert(info))) closeStream(jobId);
    } catch { /* malformed frame: ignore */ }
  });
  es.addEventListener("done", () => closeStream(jobId));
  es.onerror = () => {
    // Do NOT mark the job failed here: the server job may be perfectly alive.
    try { es.close(); } catch { /* ignore */ }
    // A late error of a stream that was already replaced or closed must not reschedule.
    if (streams.get(jobId) !== st) return;
    st.es = null;
    scheduleReconnect(jobId);
  };
}

/* ───────────────────────── hydration / polling ───────────────────────── */

const watchers = new Map<string, number>();
const hydrating = new Map<string, Promise<void>>();
const pollTimers = new Map<string, ReturnType<typeof setTimeout>>();
let globalListening = false;

/** Ask the server for one job's current state (final state, or 404 -> lost). */
async function reconcile(jobId: string): Promise<void> {
  try {
    const cur = upsert(await api.getJob(jobId));
    if (isActive(cur)) openStream(jobId);
  } catch (e) {
    if (isNotFound(e)) markLost(jobId);
    // other errors: keep the last known state, a later poll retries
  }
}

/** Re-read the project's active jobs from the server and (re)attach streams. */
export function refreshProjectJobs(projectId: string): Promise<void> {
  if (!projectId || projectId === "_") return Promise.resolve();
  const running = hydrating.get(projectId);
  if (running) return running;
  const p = (async () => {
    try {
      const list = await api.listJobs(projectId, { active: true });
      const seen = new Set<string>();
      for (const info of list) {
        seen.add(info.id);
        const cur = upsert(info);
        if (isActive(cur)) openStream(cur.id);
      }
      // Jobs we believed active that the server no longer lists: fetch their final state.
      const stale = projectSnapshot(projectId).active.filter((j) => !seen.has(j.id));
      await Promise.all(stale.map((j) => reconcile(j.id)));
    } catch {
      /* server unreachable: keep what we know */
    } finally {
      readyProjects.add(projectId);
      hydrating.delete(projectId);
      emit(projectId);
    }
  })();
  hydrating.set(projectId, p);
  return p;
}

function schedulePoll(projectId: string): void {
  const old = pollTimers.get(projectId);
  if (old) clearTimeout(old);
  pollTimers.delete(projectId);
  if (!watchers.get(projectId)) return;
  const anyActive = projectSnapshot(projectId).active.length > 0;
  pollTimers.set(projectId, setTimeout(async () => {
    await refreshProjectJobs(projectId);
    schedulePoll(projectId);
  }, anyActive ? POLL_ACTIVE_MS : POLL_IDLE_MS));
}

function refreshWatched(): void {
  for (const pid of Array.from(watchers.keys())) void refreshProjectJobs(pid);
}

function onVisibility(): void {
  if (typeof document !== "undefined" && document.visibilityState === "visible") refreshWatched();
}

function setGlobalListeners(on: boolean): void {
  if (typeof window === "undefined" || globalListening === on) return;
  globalListening = on;
  const fn = on ? window.addEventListener.bind(window) : window.removeEventListener.bind(window);
  fn("focus", refreshWatched);
  fn("bibexpy:status", refreshWatched);
  fn("visibilitychange", onVisibility);
}

/** Start watching a project (refcounted). Returns the release function. */
export function watchProject(projectId: string): () => void {
  if (!projectId || projectId === "_" || typeof window === "undefined") return () => {};
  const n = (watchers.get(projectId) ?? 0) + 1;
  watchers.set(projectId, n);
  setGlobalListeners(true);
  void refreshProjectJobs(projectId).then(() => schedulePoll(projectId));
  let released = false;
  return () => {
    if (released) return;
    released = true;
    const left = (watchers.get(projectId) ?? 1) - 1;
    if (left > 0) { watchers.set(projectId, left); return; }
    watchers.delete(projectId);
    const timer = pollTimers.get(projectId);
    if (timer) clearTimeout(timer);
    pollTimers.delete(projectId);
    if (watchers.size === 0) setGlobalListeners(false);
  };
}

/* ───────────────────────── public helpers ───────────────────────── */

/**
 * Attach to a job that is already known (a JobInfo) or only by id: track it and follow its
 * stream. Safe to call repeatedly.
 */
export function attach(job: JobInfo | string): void {
  if (typeof job === "string") { void ensureJob(job); return; }
  const cur = upsert(job);
  if (isActive(cur)) openStream(cur.id);
}

/** Make sure a job id is tracked (fetching it once if needed) and streamed if active. */
export async function ensureJob(jobId: string): Promise<void> {
  const known = jobs.get(jobId);
  if (known) {
    if (isActive(known)) openStream(jobId);
    return;
  }
  try {
    const cur = upsert(await api.getJob(jobId));
    if (isActive(cur)) openStream(jobId);
  } catch (e) {
    if (isNotFound(e) && !jobs.has(jobId)) {
      // Unknown to the server (restart): surface it as a lost job instead of spinning forever.
      const lost: StoreJob = {
        ...placeholder("", jobId), status: "failed", lost: true, error_code: "job_lost",
      };
      jobs.set(jobId, lost);
      emit(null);
    }
  }
}

/**
 * Start a job through any API call that resolves to `{ job_id }` and begin tracking it
 * right away. Rejects (unchanged) when the start request fails, e.g. 409 job_already_running.
 */
export async function start(projectId: string, starter: Promise<{ job_id: string }>): Promise<string> {
  const { job_id } = await starter;
  if (!jobs.has(job_id)) {
    // The placeholder is active, so a job that ends quickly is still announced as a
    // transition (finish listeners, toast) when its real state is merged in below.
    upsert(placeholder(projectId, job_id));
    // Read the real job (kind, title, exclusive) right away: with only the anonymous
    // placeholder, kind-based lookups ("is an enrichment running?") would report idle for the
    // moment before the first stream frame arrives, and the start button would re-enable.
    try {
      upsert(await api.getJob(job_id));
    } catch { /* the stream will deliver the real state */ }
  }
  const known = jobs.get(job_id);
  if (!known || isActive(known)) openStream(job_id);
  return job_id;
}

/** Cancel a job on the server, then pick up its new state. Errors propagate to the caller. */
export async function cancelJob(jobId: string): Promise<void> {
  await api.cancelJob(jobId);
  await reconcile(jobId);
}

/* ───────────────────────── hooks ───────────────────────── */

/**
 * Jobs of a project, kept live. Mount it on any page that needs to know about running
 * jobs; the store keeps tracking them across navigation either way.
 */
export function useProjectJobs(projectId: string): ProjectJobs {
  const pathname = usePathname();
  useEffect(() => watchProject(projectId), [projectId]);
  // Route change: the user may have come back to a project page after being away.
  useEffect(() => {
    if (projectId && projectId !== "_") void refreshProjectJobs(projectId);
  }, [projectId, pathname]);
  return useSyncExternalStore(
    subscribe,
    () => projectSnapshot(projectId),
    () => EMPTY,
  );
}

/** One job by id (re-attach safe: fetches and streams it if the store does not know it yet). */
export function useJob(jobId: string | null): StoreJob | null {
  useEffect(() => {
    if (jobId) void ensureJob(jobId);
  }, [jobId]);
  return useSyncExternalStore(
    subscribe,
    () => (jobId ? jobs.get(jobId) ?? null : null),
    () => null,
  );
}

/* ───────────────────────── display helpers ───────────────────────── */

type TFn = (k: string, v?: Record<string, string | number>) => string;

/** One-line status: t(status_key, status_params) when present, else the last log line. */
export function jobStatusLine(t: TFn, job: StoreJob, locale?: Locale): string | null {
  if (job.status_key) {
    let params = (job.status_params ?? undefined) as Record<string, string | number> | undefined;
    if (params && locale) {
      // Counts arrive as plain numbers: group them in the app language ("3,735" / "3.735").
      const formatted: Record<string, string | number> = {};
      for (const [k, v] of Object.entries(params)) {
        formatted[k] = typeof v === "number" ? formatNumber(v, locale) : v;
      }
      params = formatted;
    }
    const v = t(job.status_key, params);
    if (v !== job.status_key) return v;
  }
  const last = job.log_tail.length > 0 ? job.log_tail[job.log_tail.length - 1] : "";
  return last || null;
}

/** Translated, traceback-free error text of a failed job (or null when it did not fail). */
export function jobErrorText(t: TFn, job: StoreJob): string | null {
  if (job.lost) return t("jobs.lost");
  if (job.status !== "failed" && !job.error) return null;
  if (job.error) return translateApiError(t, new Error(job.error));
  return t("errors.generic");
}

/** Project page that shows a job's progress/result, or null when there is none. */
export function jobOwnerHref(job: { kind: string }, projectId: string): string | null {
  if (job.kind === "enrich_api" || job.kind.startsWith("disambiguate")) return `/projects/${projectId}/enrich/`;
  if (job.kind === "merge") return `/projects/${projectId}/merge/`;
  return null;
}

/** Is `pathname` the page returned by `jobOwnerHref` (ignoring a trailing slash)? */
export function isOnOwnerPage(pathname: string | null, href: string | null): boolean {
  if (!pathname || !href) return false;
  const strip = (s: string) => s.replace(/\/+$/, "");
  return strip(pathname) === strip(href);
}
