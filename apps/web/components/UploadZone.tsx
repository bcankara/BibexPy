"use client";
import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import { Upload, CheckCircle2, AlertCircle, Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";
import { useT } from "@/lib/i18n";
import { translateApiError } from "@/lib/api-client";

type Props = {
  onUpload: (files: File[]) => Promise<void>;
  accept?: Record<string, string[]>;
};

export function UploadZone({ onUpload, accept }: Props) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const onDrop = useCallback(async (files: File[]) => {
    setError(null); setDone(null); setBusy(true);
    try {
      await onUpload(files);
      setDone(t("data.filesUploaded", { n: files.length }));
    } catch (e: unknown) {
      setError(translateApiError(t, e, "data.uploadFailed"));
    } finally {
      setBusy(false);
    }
  }, [onUpload, t]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: accept ?? {
      "text/csv": [".csv"],
      "text/plain": [".txt", ".isi"],
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
    },
    disabled: busy,
  });

  return (
    <div
      {...getRootProps()}
      className={cn(
        "rounded-xl border-2 border-dashed bg-bg-soft p-10 text-center cursor-pointer transition",
        isDragActive ? "border-brand-500 bg-brand-50" : "border-border hover:border-brand-300 hover:bg-bg",
        busy && "opacity-60 cursor-progress"
      )}
    >
      <input {...getInputProps()} />
      <div className="w-14 h-14 rounded-full bg-[#0c2847] text-cyan-200 flex items-center justify-center mx-auto mb-3">
        {busy ? <Loader2 className="h-6 w-6 animate-spin" /> : <Upload className="h-6 w-6" />}
      </div>
      <p className="font-semibold text-ink">
        {isDragActive ? t("data.dropToUpload") : t("data.dragDrop")}
      </p>
      <p className="text-sm text-muted mt-1">
        Scopus <code className="text-xs bg-white px-1.5 py-0.5 rounded border border-border">.csv</code>,
        Web of Science <code className="text-xs bg-white px-1.5 py-0.5 rounded border border-border">.txt</code>,
        Excel <code className="text-xs bg-white px-1.5 py-0.5 rounded border border-border">.xlsx</code>
      </p>
      {done && (
        <p className="mt-4 inline-flex items-center gap-2 text-success text-sm font-medium">
          <CheckCircle2 className="h-4 w-4" /> {done}
        </p>
      )}
      {error && (
        <p className="mt-4 inline-flex items-center gap-2 text-danger text-sm">
          <AlertCircle className="h-4 w-4" /> {error}
        </p>
      )}
    </div>
  );
}
