"use client";
import { useState } from "react";
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

/**
 * Upload section of the unified "Data & Merge" step (Burak): lists + uploads the raw
 * Scopus CSV / WoS TXT files and starts **Smart Merge** directly.
 * There is no separate "Prepare" step — preparation happens implicitly in the first
 * phase of the merge job. That is why no consolidated (processed) file is SHOWN here.
 *
 *   variant="empty"  → only the large dropzone (no files yet)
 *   variant="ready"  → raw files (Scopus/WoS) + the "Smart Merge" button
 *   variant="stale"  → ready + a "new file added, merge again" warning on top
 */
export function UploadSection({
  projectId, files, variant, onStartMerge, starting, onChanged,
}: {
  projectId: string;
  files: UploadedFile[];
  variant: "empty" | "ready" | "stale";
  onStartMerge: () => void;
  starting: boolean;
  onChanged: () => void;
}) {
  const { t, locale } = useI18n();
  const confirm = useConfirm();
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Name of the file being deleted — that row's Trash button turns into a spinner + gets locked.
  const [deletingFile, setDeletingFile] = useState<string | null>(null);

  const scopus = files.filter((f) => f.kind === "scopus_csv");
  const wos = files.filter((f) => f.kind === "wos_txt");
  const hasFiles = files.length > 0;

  async function handleUpload(fs: File[]) {
    setError(null);
    try {
      await api.uploadFiles(projectId, fs);
      setAdding(false);
      onChanged();
    } catch (e) {
      setError(translateApiError(t, e, "data.uploadFailed"));
    }
  }

  async function handleDelete(name: string) {
    if (deletingFile) return;
    if (!(await confirm({ message: t("data.deleteFileConfirm", { name }), tone: "danger" }))) return;
    setError(null);
    setDeletingFile(name);
    try {
      await api.deleteFile(projectId, name);
      onChanged();
    } catch (e) {
      setError(translateApiError(t, e));
    } finally {
      setDeletingFile(null);
    }
  }

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
          <UploadZone onUpload={handleUpload} />
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
          <UploadZone onUpload={handleUpload} />
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            <RawColumn title={t("data.scopus")} sub="CSV" files={scopus} projectId={projectId} onDelete={handleDelete} deletingFile={deletingFile} t={t} locale={locale} />
            <RawColumn title={t("data.wos")} sub="TXT" files={wos} projectId={projectId} onDelete={handleDelete} deletingFile={deletingFile} t={t} locale={locale} />
          </div>
        )}

        <div className="flex items-center justify-between gap-2 pt-1">
          <Button variant="ghost" onClick={() => setAdding((v) => !v)} disabled={starting}>
            <Plus className="h-4 w-4" /> {adding ? t("common.cancel") : t("data.addMoreFiles")}
          </Button>
          <Button
            onClick={onStartMerge}
            disabled={starting || adding}
            className="bg-success px-6 text-base font-semibold hover:bg-success/90"
          >
            {starting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Brain className="h-4 w-4" />}
            {t("algoCard.startBtnSmart")}
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}

function RawColumn({ title, sub, files, projectId, onDelete, deletingFile, t, locale }: {
  title: string;
  sub: string;
  files: UploadedFile[];
  projectId: string;
  onDelete: (name: string) => void;
  deletingFile: string | null;
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
                disabled={deletingFile === f.name}
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted hover:text-danger disabled:opacity-60"
                title={t("common.delete")}
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

function ErrBanner({ msg }: { msg: string }) {
  return (
    <div className="rounded-lg border border-danger/30 bg-danger-soft px-3 py-2 text-xs text-red-700">
      {msg}
    </div>
  );
}
