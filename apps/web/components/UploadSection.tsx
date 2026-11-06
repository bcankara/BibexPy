"use client";
import { useEffect, useRef, useState } from "react";
import {
  api, type UploadedFile, translateApiError,
} from "@/lib/api-client";
import { UploadZone } from "@/components/UploadZone";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { Button } from "@/components/Button";
import {
  FileText, Trash2, Plus, Brain, AlertTriangle, Download, Loader2,
} from "lucide-react";
import { useI18n, type Locale } from "@/lib/i18n";
import { formatBytes, formatNumber } from "@/lib/i18n/format";
import { useConfirm } from "@/components/Dialogs";
import { LOCKED_CLASS, beginUpload } from "@/components/BlockingOverlay";
import {
  beginMergeStart, getProjectJobs, isBusyError, refreshProjectJobs, takeUploadFailure, useProjectBusy,
} from "@/lib/job-store";

/**
 * Upload section of the unified "Data & Merge" step (Burak): lists + uploads the raw
 * Scopus CSV / WoS TXT files and starts **Smart Merge** directly.
 * There is no separate "Prepare" step — preparation happens implicitly in the first
 * phase of the merge job. That is why no consolidated (processed) file is SHOWN here.
 *
 *   variant="empty"  → only the large dropzone (no files yet)
 *   variant="ready"  → raw files (Scopus/WoS) + the "Smart Merge" button
 *   variant="stale"  → ready + a "new file added, merge again" warning on top
 *
 * Locking: while a file transfer is in flight the whole app is blocked by a full-screen
 * overlay (and the browser warns before the tab is closed, because leaving really aborts the
 * upload). The upload state lives in the job store and the overlay is rendered by the project
 * layout, so it survives SPA navigation and the browser's back button. Starting a merge shows
 * the blocking overlay immediately (before the start request returns). While an exclusive job
 * of the project runs (or the job list is not loaded yet), every control here that changes the
 * raw files or starts a merge is disabled with a short reason.
 */
export function UploadSection({
  projectId, files, variant, onStartMerge, starting, onChanged,
}: {
  projectId: string;
  files: UploadedFile[];
  variant: "empty" | "ready" | "stale";
  /** May return a promise: the "merge starting" overlay stays until it settles. */
  onStartMerge: () => void | Promise<void>;
  starting: boolean;
  onChanged: () => void;
}) {
  const { t, locale } = useI18n();
  const confirm = useConfirm();
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Files being transferred right now (0 = none). Read from the job store, so an upload that
  // was started before a navigation is still seen when this section is mounted again.
  const { busy: jobBusy, ready: jobsReady, uploading: uploadingCount } = useProjectBusy(projectId);
  const uploading = uploadingCount > 0;
  // Synchronous guard: two drops in the same tick both see `uploading === false` in their closure.
  const uploadingRef = useRef(false);
  const locked = jobBusy || uploading;
  // Before the first job lookup finished the project counts as busy, but no job is to blame yet.
  const busyText = jobsReady ? t("lock.busyReason") : t("lock.checking");
  const busyTitle = jobBusy ? busyText : undefined;
  // Name of the file being deleted — that row's Trash button turns into a spinner + gets locked.
  const [deletingFile, setDeletingFile] = useState<string | null>(null);

  const scopus = files.filter((f) => f.kind === "scopus_csv");
  const wos = files.filter((f) => f.kind === "wos_txt");
  const hasFiles = files.length > 0;

  async function handleUpload(fs: File[]) {
    if (jobBusy || uploading || uploadingRef.current || getProjectJobs(projectId).uploading > 0 || fs.length === 0) {
      if (jobBusy) setError(busyText);
      return;
    }
    setError(null);
    uploadingRef.current = true;
    // Registers the upload in the job store (the project layout shows the blocking overlay) and
    // warns before the tab is closed or reloaded, independent of this component's life.
    const endUpload = beginUpload(projectId, fs.length);
    let failure: unknown;
    try {
      await api.uploadFiles(projectId, fs);
    } catch (e) {
      failure = e ?? new Error("upload failed");
      // 409: a job holds the project. Look it up so its overlay / banner appears right away.
      if (isBusyError(e)) void refreshProjectJobs(projectId);
    } finally {
      uploadingRef.current = false;
      // Success or failure is handled by the effect below, which also works when this section
      // was unmounted and mounted again while the transfer ran.
      endUpload(failure);
    }
  }

  // The upload ended (here or in an instance that was unmounted meanwhile): show its error or
  // refresh the file list.
  const wasUploading = useRef(uploading);
  useEffect(() => {
    const was = wasUploading.current;
    wasUploading.current = uploading;
    if (uploading || !was) return;
    const failure = takeUploadFailure(projectId);
    if (failure !== undefined) {
      setError(translateApiError(t, failure, "data.uploadFailed"));
    } else {
      setAdding(false);
    }
    onChanged();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploading, projectId]);

  // Start a merge: the blocking overlay appears at once, not when the start request returns.
  function handleStartMerge() {
    const endStart = beginMergeStart(projectId);
    let result: void | Promise<void>;
    try {
      result = onStartMerge();
    } catch (e) {
      endStart();
      throw e;
    }
    void Promise.resolve(result).finally(endStart);
  }

  async function handleDelete(name: string) {
    if (deletingFile || jobBusy || uploading) return;
    if (!(await confirm({ message: t("data.deleteFileConfirm", { name }), tone: "danger" }))) return;
    setError(null);
    setDeletingFile(name);
    try {
      await api.deleteFile(projectId, name);
      onChanged();
    } catch (e) {
      setError(translateApiError(t, e));
      if (isBusyError(e)) void refreshProjectJobs(projectId);
    } finally {
      setDeletingFile(null);
    }
  }

  // While a job holds the project the dropzone is replaced by the reason (a drop would be refused).
  const zone = jobBusy ? <BusyNote text={busyText} /> : <UploadZone onUpload={handleUpload} />;

  // Empty: only the large dropzone
  if (variant === "empty" || !hasFiles) {
    return (
      <Card>
        <CardHeader>
          <FileText className="h-4 w-4 text-muted" />
          <h2 className="font-semibold">{t("data.prepareSectionTitle")}</h2>
        </CardHeader>
        <CardBody className="space-y-3">
          {error && <ErrBanner msg={error} />}
          {zone}
        </CardBody>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <FileText className="h-4 w-4 text-muted" />
        <h2 className="font-semibold flex-1">{t("merge.uploadTitle")}</h2>
        <span className="text-xs text-muted">{formatNumber(files.length, locale)} {t("common.files")}</span>
      </CardHeader>
      <CardBody className="space-y-4">
        {error && <ErrBanner msg={error} />}
        {variant === "stale" && (
          <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 flex items-start gap-2">
            <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5" />
            <span>{t("merge.staleHint")}</span>
          </div>
        )}

        {adding ? (
          zone
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            <RawColumn title={t("data.scopus")} sub="CSV" files={scopus} projectId={projectId} onDelete={handleDelete} deletingFile={deletingFile} locked={locked} lockedTitle={busyTitle} t={t} locale={locale} />
            <RawColumn title={t("data.wos")} sub="TXT" files={wos} projectId={projectId} onDelete={handleDelete} deletingFile={deletingFile} locked={locked} lockedTitle={busyTitle} t={t} locale={locale} />
          </div>
        )}

        <div className="flex items-center justify-between gap-2 pt-1">
          <Button variant="ghost" onClick={() => setAdding((v) => !v)} disabled={starting || locked} title={busyTitle} className={LOCKED_CLASS}>
            <Plus className="h-4 w-4" /> {adding ? t("common.cancel") : t("data.addMoreFiles")}
          </Button>
          <Button
            onClick={handleStartMerge}
            disabled={starting || adding || locked}
            title={busyTitle}
            className={`bg-success px-6 text-base font-semibold hover:bg-success/90 ${LOCKED_CLASS}`}
          >
            {starting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Brain className="h-4 w-4" />}
            {t("algoCard.startBtnSmart")}
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}

function RawColumn({ title, sub, files, projectId, onDelete, deletingFile, locked, lockedTitle, t, locale }: {
  title: string;
  sub: string;
  files: UploadedFile[];
  projectId: string;
  onDelete: (name: string) => void;
  deletingFile: string | null;
  /** A job or an upload holds the project: deleting a raw file is disabled. */
  locked: boolean;
  lockedTitle?: string;
  t: (k: string, vars?: Record<string, string | number>) => string;
  locale: Locale;
}) {
  return (
    <div className="rounded-xl border border-border bg-bg-soft/40 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
        {title} <span className="text-muted/60">· {sub}</span>
      </div>
      {files.length === 0 ? (
        <div className="py-1 text-xs italic text-muted/60">{t("data.noFilesYet")}</div>
      ) : (
        <ul className="space-y-1">
          {files.map((f) => (
            <li key={f.name} className="flex items-center gap-2">
              <FileText className="h-4 w-4 flex-shrink-0 text-muted" />
              <span className="flex-1 truncate font-mono text-xs" title={f.name}>{f.name}</span>
              <span className="text-[11px] text-muted tabular-nums">{formatBytes(f.size, locale)}</span>
              <a
                href={api.downloadFromUrl(projectId, "raw", f.name)}
                download={f.name}
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted hover:bg-brand-50 hover:text-brand-600"
                title={t("common.download")}
              >
                <Download className="h-3.5 w-3.5" />
              </a>
              <button
                onClick={() => onDelete(f.name)}
                disabled={deletingFile === f.name || locked}
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted hover:text-danger disabled:opacity-60"
                title={lockedTitle ?? t("common.delete")}
              >
                {deletingFile === f.name ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function BusyNote({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-cyan-200 bg-cyan-50 px-3 py-3 text-xs text-cyan-900">
      <Loader2 className="h-3.5 w-3.5 flex-shrink-0 animate-spin" />
      {text}
    </div>
  );
}

function ErrBanner({ msg }: { msg: string }) {
  return (
    <div className="rounded-lg border border-danger/30 bg-danger-soft px-3 py-2 text-xs text-red-700">
      {msg}
    </div>
  );
}
