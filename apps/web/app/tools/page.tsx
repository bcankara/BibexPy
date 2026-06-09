"use client";
import { useRef, useState } from "react";
import { api, formatBytes, translateApiError} from "@/lib/api-client";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { Button } from "@/components/Button";
import { PageHeader } from "@/components/PageHeader";
import { ToolBadgeList, type ToolKey } from "@/components/ToolBadge";
import {
  Wrench, Upload, Loader2, CheckCircle2, FileText, Download, X,
  FileSpreadsheet, FileType, BookOpen, FileJson, Database, AlertCircle,
} from "lucide-react";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/cn";

const MAX_BYTES = 100 * 1024 * 1024; // 100 MB

type SourceId = "wos" | "scopus_csv" | "xlsx" | "csv" | "tsv";
type TargetId = "wos" | "vos" | "bib" | "ris" | "xlsx" | "csv" | "tsv";

const SOURCE_OPTIONS: { id: SourceId; icon: React.ReactNode; exts: string[] }[] = [
  { id: "wos",        icon: <FileText className="h-4 w-4" />,         exts: ["txt", "isi"] },
  { id: "scopus_csv", icon: <FileType className="h-4 w-4" />,         exts: ["csv"] },
  { id: "xlsx",       icon: <FileSpreadsheet className="h-4 w-4" />,  exts: ["xlsx"] },
  { id: "csv",        icon: <FileType className="h-4 w-4" />,         exts: ["csv"] },
  { id: "tsv",        icon: <FileType className="h-4 w-4" />,         exts: ["tsv", "txt"] },
];

const TARGET_OPTIONS: {
  id: TargetId;
  icon: React.ReactNode;
  primary: ToolKey[];
  secondary: ToolKey[];
}[] = [
  {
    id: "wos",  icon: <FileText className="h-4 w-4" />,
    primary: ["bibliometrix", "biblioshiny", "vosviewer"],
    secondary: ["citespace", "histcite", "bibexcel"],
  },
  {
    id: "vos",  icon: <Database className="h-4 w-4" />,
    primary: ["vosviewer"],
    secondary: ["gephi", "openrefine"],
  },
  {
    id: "bib",  icon: <BookOpen className="h-4 w-4" />,
    primary: ["zotero", "jabref", "mendeley", "latex", "overleaf"],
    secondary: ["endnote", "citavi"],
  },
  {
    id: "ris",  icon: <FileJson className="h-4 w-4" />,
    primary: ["endnote", "zotero", "mendeley", "citavi"],
    secondary: ["refworks", "papers"],
  },
  {
    id: "xlsx", icon: <FileSpreadsheet className="h-4 w-4" />,
    primary: ["excel", "biblioshiny", "tableau"],
    secondary: ["powerbi", "python", "r"],
  },
  {
    id: "csv",  icon: <FileType className="h-4 w-4" />,
    primary: ["biblioshiny", "python", "r", "excel"],
    secondary: ["openrefine", "tableau", "powerbi"],
  },
  {
    id: "tsv",  icon: <FileType className="h-4 w-4" />,
    primary: ["vosviewer", "openrefine"],
    secondary: ["python", "r", "excel"],
  },
];

/** Yüklenen dosya uzantısından makul bir source format öner. */
function guessSource(filename: string): SourceId | null {
  const lower = filename.toLowerCase();
  const ext = lower.split(".").pop() || "";
  if (ext === "xlsx" || ext === "xls") return "xlsx";
  if (ext === "tsv") return "tsv";
  if (ext === "txt" || ext === "isi") return "wos";
  if (ext === "csv") {
    // Scopus CSV vs plain CSV — kesin söylenemez, kullanıcı seçsin. Default: scopus_csv.
    return null;
  }
  return null;
}

export default function ToolsPage() {
  const t = useT();
  const [file, setFile] = useState<File | null>(null);
  const [source, setSource] = useState<SourceId | null>(null);
  const [target, setTarget] = useState<TargetId>("wos");
  const [outputName, setOutputName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ blob: Blob; filename: string; records: number; columns: number } | null>(null);
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function onPickFile(f: File | null) {
    setError(null);
    setResult(null);
    if (!f) { setFile(null); setSource(null); return; }
    if (f.size > MAX_BYTES) {
      setError(t("tools.fileTooBig"));
      return;
    }
    setFile(f);
    const guess = guessSource(f.name);
    if (guess && !source) setSource(guess);
  }

  async function runConvert() {
    if (!file) { setError(t("tools.noFileSelected")); return; }
    if (!source) { setError(t("tools.step2Title")); return; }
    setBusy(true); setError(null); setResult(null);
    try {
      const r = await api.toolsConvert(file, source, target, outputName.trim() || undefined);
      setResult(r);
      // Otomatik indirme tetikle
      triggerDownload(r.blob, r.filename);
    } catch (e) {
      setError(translateApiError(t, e, "tools.errTitle"));
    } finally { setBusy(false); }
  }

  function downloadAgain() {
    if (result) triggerDownload(result.blob, result.filename);
  }

  function reset() {
    setFile(null); setSource(null); setOutputName("");
    setResult(null); setError(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  const currentTargetMeta = TARGET_OPTIONS.find((o) => o.id === target)!;

  return (
    <>
      <PageHeader
        title={t("tools.title")}
        subtitle={t("tools.subtitle")}
        badges={[{ label: "Beta", tone: "neutral" }]}
      />
      <div className="max-w-5xl mx-auto px-6 py-8 space-y-6">

        {/* Converter card */}
        <Card>
          <CardHeader>
            <Wrench className="h-4 w-4 text-brand-600" />
            <h2 className="font-semibold text-sm flex-1">{t("tools.converterTitle")}</h2>
          </CardHeader>
          <CardBody className="space-y-6">
            <p className="text-sm text-muted leading-relaxed">{t("tools.converterSubtitle")}</p>

            {/* Step 1 — File */}
            <section>
              <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted mb-1.5">
                {t("tools.step1Title")}
              </h3>
              <div
                onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
                onDragLeave={() => setDrag(false)}
                onDrop={(e) => {
                  e.preventDefault(); setDrag(false);
                  const f = e.dataTransfer.files[0];
                  if (f) onPickFile(f);
                }}
                onClick={() => inputRef.current?.click()}
                className={cn(
                  "rounded-xl border-2 border-dashed transition cursor-pointer",
                  "px-6 py-8 flex flex-col items-center gap-2 text-center",
                  drag
                    ? "border-brand-500 bg-brand-50"
                    : file
                      ? "border-success/40 bg-success-soft/30"
                      : "border-border bg-bg-soft/40 hover:border-brand-300 hover:bg-brand-50/40",
                )}
              >
                <input
                  ref={inputRef}
                  type="file"
                  accept=".txt,.csv,.tsv,.xlsx,.xls,.isi"
                  onChange={(e) => onPickFile(e.target.files?.[0] ?? null)}
                  className="hidden"
                />
                {file ? (
                  <>
                    <CheckCircle2 className="h-7 w-7 text-success" />
                    <p className="text-sm font-medium text-ink">
                      {t("tools.fileSelected", { name: file.name, size: formatBytes(file.size) })}
                    </p>
                    <button
                      onClick={(e) => { e.stopPropagation(); reset(); }}
                      className="text-[11px] text-muted hover:text-danger underline mt-1"
                    >
                      <X className="h-3 w-3 inline mr-0.5" />
                      {t("common.cancel")}
                    </button>
                  </>
                ) : (
                  <>
                    <Upload className="h-7 w-7 text-muted/60" />
                    <p className="text-sm font-medium text-ink">
                      {drag ? t("tools.dropToUpload") : t("tools.dropOrClick")}
                    </p>
                    <p className="text-[11px] text-muted">{t("tools.step1Hint")}</p>
                  </>
                )}
              </div>
            </section>

            {/* Step 2 — Source format */}
            <section>
              <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted mb-1.5">
                {t("tools.step2Title")}
              </h3>
              <p className="text-xs text-muted mb-2">{t("tools.step2Hint")}</p>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                {SOURCE_OPTIONS.map((opt) => {
                  const selected = source === opt.id;
                  return (
                    <button
                      key={opt.id}
                      onClick={() => setSource(opt.id)}
                      disabled={busy}
                      className={cn(
                        "text-left rounded-lg border-2 px-3 py-2 transition disabled:opacity-50",
                        selected
                          ? "border-brand-500 bg-brand-50/60"
                          : "border-border bg-white hover:border-brand-300",
                      )}
                    >
                      <div className="flex items-center gap-1.5 mb-0.5">
                        <span className={selected ? "text-brand-600" : "text-muted"}>{opt.icon}</span>
                        <span className={cn("text-xs font-semibold", selected && "text-brand-700")}>
                          {t(`tools.sourceFormats.${opt.id}`)}
                        </span>
                      </div>
                      <div className="text-[10px] text-muted font-mono">
                        {opt.exts.map((e) => `.${e}`).join(" / ")}
                      </div>
                    </button>
                  );
                })}
              </div>
            </section>

            {/* Step 3 — Target format */}
            <section>
              <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted mb-1.5">
                {t("tools.step3Title")}
              </h3>
              <p className="text-xs text-muted mb-2">{t("tools.step3Hint")}</p>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
                {TARGET_OPTIONS.map((opt) => {
                  const selected = target === opt.id;
                  return (
                    <button
                      key={opt.id}
                      onClick={() => setTarget(opt.id)}
                      disabled={busy}
                      className={cn(
                        "text-left rounded-lg border-2 px-3 py-2 transition disabled:opacity-50",
                        selected
                          ? "border-brand-500 bg-brand-50/60 shadow-soft"
                          : "border-border bg-white hover:border-brand-300",
                      )}
                    >
                      <div className="flex items-center gap-1.5">
                        <span className={selected ? "text-brand-600" : "text-muted"}>{opt.icon}</span>
                        <span className={cn("text-xs font-semibold", selected && "text-brand-700")}>
                          {t(`tools.targetFormats.${opt.id}`)}
                        </span>
                      </div>
                    </button>
                  );
                })}
              </div>

              {/* Output name */}
              <div className="mt-3">
                <label className="block text-[11px] text-muted mb-1">{t("tools.outputName")}</label>
                <input
                  value={outputName}
                  onChange={(e) => setOutputName(e.target.value)}
                  placeholder={t("tools.outputNamePlaceholder")}
                  disabled={busy}
                  className="w-full rounded-md border border-border bg-white px-3 py-1.5 text-sm focus:outline-none focus:border-brand-500 disabled:opacity-50"
                />
              </div>
            </section>

            {/* Hata */}
            {error && (
              <div className="rounded-lg border border-danger/30 bg-danger-soft px-4 py-2 text-sm text-red-700 flex items-start gap-2">
                <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                <div>
                  <div className="font-medium">{t("tools.errTitle")}</div>
                  <div className="text-xs text-red-700/80 mt-0.5">{error}</div>
                </div>
              </div>
            )}

            {/* Sonuç */}
            {result && (
              <div className="rounded-lg border border-success/30 bg-success-soft/40 px-4 py-3 text-sm flex items-start gap-3">
                <CheckCircle2 className="h-5 w-5 text-success mt-0.5 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="font-semibold text-emerald-800">
                    {t("tools.successMsg", { records: result.records, columns: result.columns })}
                  </div>
                  <div className="text-xs text-emerald-700/80 mt-0.5 font-mono truncate">{result.filename}</div>
                </div>
                <Button size="sm" variant="secondary" onClick={downloadAgain}>
                  <Download className="h-3.5 w-3.5" />
                  {t("tools.downloadAgain")}
                </Button>
                <Button size="sm" variant="ghost" onClick={reset}>
                  {t("tools.resetBtn")}
                </Button>
              </div>
            )}

            {/* Convert button */}
            <div className="flex justify-end pt-2 border-t border-border">
              <Button
                onClick={runConvert}
                disabled={busy || !file || !source}
                size="md"
              >
                {busy
                  ? <><Loader2 className="h-4 w-4 animate-spin" /> {t("tools.converting")}</>
                  : <><Download className="h-4 w-4" /> {t("tools.convertBtn")}</>
                }
              </Button>
            </div>
          </CardBody>
        </Card>

        {/* Tool ecosystem card — kullanıcı hedef formata göre hangi araçlarda
            kullanabileceğini görür. Seçim değiştikçe gösterilen liste değişir. */}
        <Card>
          <CardHeader>
            <h2 className="font-semibold text-sm flex-1">{t("tools.section.ecosystem")}</h2>
            <span className="text-xs text-muted">{t(`tools.targetFormats.${target}`)}</span>
          </CardHeader>
          <CardBody>
            <p className="text-xs text-muted mb-3">{t("tools.section.ecosystemHint")}</p>
            <ToolBadgeList
              primary={currentTargetMeta.primary}
              secondary={currentTargetMeta.secondary}
            />
          </CardBody>
        </Card>

      </div>
    </>
  );
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}
