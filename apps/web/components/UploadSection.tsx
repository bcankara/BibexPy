"use client";
import { useState } from "react";
import {
  api, formatBytes, type UploadedFile, translateApiError,
} from "@/lib/api-client";
import { UploadZone } from "@/components/UploadZone";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { Button } from "@/components/Button";
import {
  FileText, Trash2, Plus, Brain, AlertTriangle, Download, Loader2,
} from "lucide-react";
import { useT } from "@/lib/i18n";
import { useConfirm } from "@/components/Dialogs";

/**
 * Birleşik "Veri & Birleştirme" adımının yükleme bölümü (Burak): ham Scopus CSV /
 * WoS TXT dosyalarını listeler + yükler ve doğrudan **Smart Merge** başlatır.
 * Ayrı "Prepare" adımı yok — hazırlık merge job'unun ilk fazında örtük yapılır.
 * Bu yüzden burada konsolide (processed) dosya GÖSTERİLMEZ.
 *
 *   variant="empty"  → yalnız büyük dropzone (henüz dosya yok)
 *   variant="ready"  → ham dosyalar (Scopus/WoS) + "Smart Merge" butonu
 *   variant="stale"  → ready + üstte "yeni dosya eklendi, yeniden birleştir" uyarısı
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
  const t = useT();
  const confirm = useConfirm();
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    if (!(await confirm({ message: t("data.deleteFileConfirm", { name }), tone: "danger" }))) return;
    setError(null);
    try {
      await api.deleteFile(projectId, name);
      onChanged();
    } catch (e) {
      setError(translateApiError(t, e));
    }
  }

  // Boş: yalnız büyük dropzone
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
        <span className="text-xs text-muted">{files.length} {t("common.files")}</span>
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
            <RawColumn title={t("data.scopus")} sub="CSV" files={scopus} projectId={projectId} onDelete={handleDelete} t={t} />
            <RawColumn title={t("data.wos")} sub="TXT" files={wos} projectId={projectId} onDelete={handleDelete} t={t} />
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

function RawColumn({ title, sub, files, projectId, onDelete, t }: {
  title: string;
  sub: string;
  files: UploadedFile[];
  projectId: string;
  onDelete: (name: string) => void;
  t: (k: string, vars?: Record<string, string | number>) => string;
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
              <span className="text-[11px] text-muted tabular-nums">{formatBytes(f.size)}</span>
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
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted hover:text-danger"
                title={t("common.delete")}
              >
                <Trash2 className="h-3.5 w-3.5" />
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
