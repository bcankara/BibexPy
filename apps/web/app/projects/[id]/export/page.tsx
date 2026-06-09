"use client";
import { useEffect, useState } from "react";
import {
  api, formatBytes, type ConvertResult, type ExportFormat,
  type Preset, type FilterSpec, type FolderSuggest, translateApiError,
} from "@/lib/api-client";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { Button } from "@/components/Button";
import { PageHeader } from "@/components/PageHeader";
import {
  Download, FileText, Loader2, FolderOpen, Copy, CheckCircle2,
  FileSpreadsheet, FileType, BookOpen, FileJson, Database,
  FolderInput, Check, X, AlertCircle, Save,
} from "lucide-react";
import { useT } from "@/lib/i18n";
import { useConfirm, useToast } from "@/components/Dialogs";
import { type ToolKey, ToolBadgeList } from "@/components/ToolBadge";
import { StepNav } from "@/components/StepNav";
import { AuditLogPanel } from "@/components/AuditLogPanel";
import { useProjectId } from "@/lib/use-project-id";

// File System Access API tipleri (TS 5+ ile gelir ama her zaman değil)
declare global {
  interface Window {
    showDirectoryPicker?: (options?: { mode?: "read" | "readwrite"; startIn?: string }) => Promise<FileSystemDirectoryHandle>;
  }
}

type FormatInfo = {
  fmt: ExportFormat;
  label: string;
  info: string;          // runtime'da t() ile doldurulur
  group: "ref" | "table";
  icon: React.ReactNode;
  /** Mayıs 2026 itibariyle bu formatla doğrudan çalışan araçlar */
  primary: ToolKey[];
  secondary: ToolKey[];
};

type FormatStatic = {
  fmt: ExportFormat;
  label: string;
  group: "ref" | "table";
  icon: React.ReactNode;
  primary: ToolKey[];
  secondary: ToolKey[];
};

/**
 * Format → araç ekosistemi haritası (Mayıs 2026 itibariyle).
 *
 * "primary": format-natif olarak veya tek tıkla içe aktarılan araçlar.
 * "secondary": ek dönüşüm/aracı ile destekleyenler — daha az yaygın.
 *
 * Kaynaklar: VOSviewer (Van Eck & Waltman), bibliometrix/biblioshiny (Aria & Cuccurullo 2017,
 * "Science Mapping Analysis – A Primer with Biblioshiny" McGraw-Hill 2026), CiteSpace (Chen),
 * BibExcel (Persson), HistCite (Garfield), Zotero/Mendeley/EndNote import docs.
 */
const FORMATS_STATIC: FormatStatic[] = [
  {
    fmt: "wos", label: "WoS plain text", group: "ref",
    icon: <FileText className="h-5 w-5" />,
    primary:   ["bibliometrix", "biblioshiny", "vosviewer"],
    secondary: ["citespace", "histcite", "bibexcel", "citnetexplorer"],
  },
  {
    fmt: "vos", label: "VOSviewer TSV", group: "ref",
    icon: <Database className="h-5 w-5" />,
    primary:   ["vosviewer"],
    secondary: ["gephi", "openrefine"],
  },
  {
    fmt: "bib", label: "BibTeX (.bib)", group: "ref",
    icon: <BookOpen className="h-5 w-5" />,
    primary:   ["zotero", "jabref", "mendeley", "latex", "overleaf"],
    secondary: ["endnote", "citavi", "bibliometrix"],
  },
  {
    fmt: "ris", label: "RIS", group: "ref",
    icon: <FileJson className="h-5 w-5" />,
    primary:   ["endnote", "zotero", "mendeley", "citavi"],
    secondary: ["refworks", "papers"],
  },
  {
    fmt: "xlsx", label: "Excel (.xlsx)", group: "table",
    icon: <FileSpreadsheet className="h-5 w-5" />,
    primary:   ["excel", "biblioshiny", "tableau"],
    secondary: ["powerbi", "python", "r"],
  },
  {
    fmt: "csv", label: "CSV", group: "table",
    icon: <FileType className="h-5 w-5" />,
    primary:   ["biblioshiny", "python", "r", "excel"],
    secondary: ["openrefine", "tableau", "powerbi"],
  },
  {
    fmt: "tsv", label: "TSV", group: "table",
    icon: <FileType className="h-5 w-5" />,
    primary:   ["vosviewer", "openrefine"],
    secondary: ["python", "r", "excel"],
  },
];

type CopyMode = "browser" | "path";

export default function ExportPage() {
  const id = useProjectId();
  const t = useT();
  const confirm = useConfirm();
  const toast = useToast();
  const [exports, setExports] = useState<ConvertResult[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [folderSuggest, setFolderSuggest] = useState<FolderSuggest | null>(null);
  const [selectedPreset, setSelectedPreset] = useState<string>("");
  const [outputFolder, setOutputFolder] = useState<string>("");
  const [busy, setBusy] = useState<ExportFormat | "copy" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAudit, setShowAudit] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // File System Access API state
  const [dirHandle, setDirHandle] = useState<FileSystemDirectoryHandle | null>(null);
  const [copyProgress, setCopyProgress] = useState<{ done: number; total: number; current?: string } | null>(null);
  const supportsFSA = typeof window !== "undefined" && !!window.showDirectoryPicker;
  // Duplicate file dialog: aynı format için mevcut dosyalar olduğunda kullanıcıya sor
  const [dupDialog, setDupDialog] = useState<{ fmt: ExportFormat; existing: ConvertResult[] } | null>(null);
  const [mode, setMode] = useState<CopyMode>(supportsFSA ? "browser" : "path");

  async function refresh() {
    try {
      const [e, p, f] = await Promise.all([
        api.listExports(id),
        api.listPresets(id),
        api.suggestFolders(id),
      ]);
      setExports(e); setPresets(p); setFolderSuggest(f);
      if (!outputFolder && f.suggestions.length > 0) {
        setOutputFolder(f.suggestions[0].path);
      }
    } catch {}
  }
  useEffect(() => { refresh(); /* eslint-disable-next-line */ }, [id]);

  const activeSpec: FilterSpec | undefined = selectedPreset
    ? presets.find((p) => p.name === selectedPreset)?.spec
    : undefined;

  /** Aynı format için mevcut dosyalar varsa dialog açar; yoksa direkt export. */
  function runExport(fmt: ExportFormat) {
    const existingSame = exports.filter((e) => e.name.toLowerCase().endsWith(`.${fmt}`));
    if (existingSame.length > 0) {
      setDupDialog({ fmt, existing: existingSame });
      return;
    }
    doExport(fmt, "rename");
  }

  async function doExport(fmt: ExportFormat, if_exists: "rename" | "replace" | "abort") {
    setBusy(fmt); setError(null);
    setDupDialog(null);
    try {
      const result = await api.createExport(id, fmt, activeSpec, undefined, if_exists);
      await refresh();
      // Burak: export'a basınca DİREKT indir — kullanıcı altta dosyayı aramasın/beklemesin.
      const name = (result as { name?: string } | undefined)?.name;
      if (name) {
        const a = document.createElement("a");
        a.href = api.downloadFromUrl(id, "exports", name);
        a.download = name;
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
    } catch (e) {
      setError(translateApiError(t, e, "export.exportFailed"));
    } finally { setBusy(null); }
  }

  async function deleteOneExport(name: string) {
    if (!(await confirm({ message: t("export.deleteExportConfirm", { name }), tone: "danger" }))) return;
    try {
      await api.deleteExport(id, name);
      setSelected((prev) => { const n = new Set(prev); n.delete(name); return n; });
      await refresh();
    } catch (e) {
      setError(translateApiError(t, e, "export.deleteExportFailed"));
    }
  }

  function toggle(name: string) {
    const next = new Set(selected);
    if (next.has(name)) next.delete(name); else next.add(name);
    setSelected(next);
  }

  async function copyToFolder() {
    if (selected.size === 0 || !outputFolder.trim()) return;
    setBusy("copy"); setError(null);
    try {
      const r = await api.copyToFolder(id, [...selected], outputFolder.trim());
      if (r.skipped.length > 0) {
        toast(r.skipped.map(s => `• ${s.name}: ${s.reason}`).join("\n"), {
          tone: "warning",
          title: `${t("export.copied", { n: r.copied.length })} · ${t("export.skipped")}`,
        });
      } else {
        toast(r.target_folder, { tone: "success", title: t("export.copied", { n: r.copied.length }) });
      }
      setSelected(new Set());
    } catch (e) {
      setError(translateApiError(t, e, "export.copyFailed"));
    } finally { setBusy(null); }
  }

  async function pickFolder() {
    if (!window.showDirectoryPicker) {
      setError(t("export.browserNotSupported"));
      return;
    }
    try {
      const handle = await window.showDirectoryPicker({ mode: "readwrite" });
      setDirHandle(handle);
      setError(null);
    } catch (e) {
      // User cancelled (AbortError) — silent
      if ((e as DOMException).name !== "AbortError") {
        setError(translateApiError(t, e, "export.folderPickFailed"));
      }
    }
  }

  async function copyToBrowserFolder() {
    if (!dirHandle || selected.size === 0) return;
    setBusy("copy"); setError(null);
    const files = [...selected];
    setCopyProgress({ done: 0, total: files.length });

    let succeeded: string[] = [];
    let failed: { name: string; reason: string }[] = [];

    try {
      for (let i = 0; i < files.length; i++) {
        const name = files[i];
        setCopyProgress({ done: i, total: files.length, current: name });
        try {
          // Backend'den dosyayı fetch et
          const res = await fetch(api.downloadFromUrl(id, "exports", name));
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const blob = await res.blob();

          // Klasöre yaz
          const fileHandle = await dirHandle.getFileHandle(name, { create: true });
          const writable = await fileHandle.createWritable();
          await writable.write(blob);
          await writable.close();
          succeeded.push(name);
        } catch (e) {
          failed.push({ name, reason: translateApiError(t, e) });
        }
      }
      setCopyProgress({ done: files.length, total: files.length });

      // Audit log
      try {
        await api.addAuditEntry(id, {
          kind: "export",
          title: t("export.browserCopyTitle", { n: succeeded.length }),
          details: {
            method: "browser_fsa",
            target_folder_name: dirHandle.name,
            copied_count: succeeded.length,
            skipped_count: failed.length,
            files: succeeded,
            skipped: failed,
          },
          user_action: "copy_browser",
        });
      } catch {}

      if (failed.length > 0) {
        setError(
          `${t("export.copiedFailed", { ok: succeeded.length, fail: failed.length })}:\n` +
          failed.map((f) => `• ${f.name}: ${f.reason}`).join("\n"),
        );
      } else {
        setTimeout(() => setCopyProgress(null), 2500);
      }
      setSelected(new Set());
    } catch (e) {
      setError(translateApiError(t, e, "export.copyFailed"));
    } finally {
      setBusy(null);
    }
  }

  function startCopy() {
    if (mode === "browser") copyToBrowserFolder();
    else copyToFolder();
  }

  return (
    <>
      <PageHeader
        title={t("export.title")}
        subtitle={t("export.subtitle")}
        badges={[{ label: t("export.stepBadge"), tone: "neutral" }]}
        right={<StepNav onHistory={() => setShowAudit(true)} />}
      />
      <div className="max-w-6xl mx-auto px-6 py-8 space-y-6">

      {error && <div className="rounded-lg border border-danger/30 bg-danger-soft px-4 py-2 text-sm text-red-700">{error}</div>}

      {/* Filtre seçimi — sade kontrol şeridi (ağır kart yerine) */}
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-white px-4 py-3 shadow-soft">
        <span className="mr-1 text-[11px] font-semibold uppercase tracking-wide text-muted">{t("export.filterSelection")}</span>
        <button
          onClick={() => setSelectedPreset("")}
          className={`text-xs px-3 py-1.5 rounded-lg border transition ${
            !selectedPreset ? "bg-brand-50 border-brand-500 text-brand-700 font-medium" : "bg-white border-border text-muted hover:text-ink"
          }`}
        >
          {t("export.allData")}
        </button>
        {presets.length === 0 ? (
          <span className="text-xs text-muted">{t("export.noPresetsHint")}</span>
        ) : presets.map((p) => (
          <button
            key={p.name}
            onClick={() => setSelectedPreset(p.name)}
            className={`text-xs px-3 py-1.5 rounded-lg border transition ${
              selectedPreset === p.name ? "bg-brand-50 border-brand-500 text-brand-700 font-medium" : "bg-white border-border text-muted hover:text-ink"
            }`}
          >
            {p.name}
          </button>
        ))}
        <span className="ml-auto text-xs text-muted">
          {activeSpec ? t("export.subsetExported") : t("export.allExported")}
        </span>
      </div>

      {/* Referans formatları */}
      <Card>
        <CardHeader>
          <h2 className="font-semibold text-sm">{t("export.refFormats")}</h2>
        </CardHeader>
        <CardBody>
          <div className="grid sm:grid-cols-2 gap-2">
            {FORMATS_STATIC.filter(f => f.group === "ref").map((f) => (
              <FormatCard
                key={f.fmt}
                f={{ ...f, info: t(`export.formatInfo.${f.fmt}`) }}
                busy={busy === f.fmt}
                onClick={() => runExport(f.fmt)}
              />
            ))}
          </div>
        </CardBody>
      </Card>

      {/* Genel tablo formatları */}
      <Card>
        <CardHeader>
          <h2 className="font-semibold text-sm">{t("export.tableFormats")}</h2>
        </CardHeader>
        <CardBody>
          <div className="grid sm:grid-cols-2 gap-2">
            {FORMATS_STATIC.filter(f => f.group === "table").map((f) => (
              <FormatCard
                key={f.fmt}
                f={{ ...f, info: t(`export.formatInfo.${f.fmt}`) }}
                busy={busy === f.fmt}
                onClick={() => runExport(f.fmt)}
              />
            ))}
          </div>
        </CardBody>
      </Card>

      </div>

      {/* Duplicate file dialog */}
      {dupDialog && (
        <DuplicateExportDialog
          fmt={dupDialog.fmt}
          existing={dupDialog.existing}
          onCancel={() => setDupDialog(null)}
          onReplace={() => doExport(dupDialog.fmt, "replace")}
          onKeep={() => doExport(dupDialog.fmt, "rename")}
        />
      )}

      <AuditLogPanel projectId={id} open={showAudit} onClose={() => setShowAudit(false)} />
    </>
  );
}

/** Aynı format için mevcut export varsa kullanıcıya 3 seçenek sunan modal. */
function DuplicateExportDialog({ fmt, existing, onCancel, onReplace, onKeep }: {
  fmt: ExportFormat;
  existing: ConvertResult[];
  onCancel: () => void;
  onReplace: () => void;
  onKeep: () => void;
}) {
  const t = useT();
  const fmtUpper = fmt.toUpperCase();
  const plural = existing.length === 1 ? "" : "s";
  return (
    <>
      <div className="fixed inset-0 bg-ink/40 backdrop-blur-sm z-[60]" onClick={onCancel} />
      <div className="fixed inset-0 z-[70] flex items-center justify-center p-4 pointer-events-none">
        <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden pointer-events-auto">
          <div className="px-5 py-3 border-b border-border bg-gradient-to-r from-warning-soft/60 to-amber-50/40 flex items-center gap-2">
            <AlertCircle className="h-5 w-5 text-warning" />
            <h3 className="font-semibold text-sm text-ink">
              {t("export.duplicate.title", { format: fmtUpper })}
            </h3>
          </div>
          <div className="px-5 py-4 space-y-3">
            <p className="text-sm text-ink">
              {t("export.duplicate.body", { count: existing.length, format: fmtUpper, plural })}
            </p>
            <ul className="text-[11px] text-muted bg-bg-soft rounded-lg px-3 py-2 max-h-32 overflow-y-auto font-mono">
              {existing.map((f) => (
                <li key={f.name} className="truncate" title={f.name}>{f.name}</li>
              ))}
            </ul>
            <div className="space-y-2 pt-1">
              <button
                onClick={onReplace}
                className="w-full text-left rounded-lg border-2 border-danger/40 bg-danger-soft/30 hover:bg-danger-soft/50 hover:border-danger transition px-3 py-2.5 group"
              >
                <div className="flex items-center gap-2 text-sm font-semibold text-red-800">
                  <X className="h-3.5 w-3.5" />
                  {t("export.duplicate.replace")}
                </div>
                <div className="text-[11px] text-red-700/70 mt-0.5 ml-5">
                  {t("export.duplicate.replaceHint")}
                </div>
              </button>
              <button
                onClick={onKeep}
                className="w-full text-left rounded-lg border-2 border-brand-300 bg-brand-50/40 hover:bg-brand-50/70 hover:border-brand-500 transition px-3 py-2.5 group"
              >
                <div className="flex items-center gap-2 text-sm font-semibold text-brand-800">
                  <FileText className="h-3.5 w-3.5" />
                  {t("export.duplicate.keep")}
                </div>
                <div className="text-[11px] text-brand-700/70 mt-0.5 ml-5">
                  {t("export.duplicate.keepHint")}
                </div>
              </button>
              <button
                onClick={onCancel}
                className="w-full text-left rounded-lg border border-border bg-white hover:bg-bg-soft transition px-3 py-2 text-sm text-muted hover:text-ink"
              >
                {t("export.duplicate.cancel")}
              </button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

function CopyProgressCard({ progress }: { progress: { done: number; total: number; current?: string } }) {
  const t = useT();
  const pct = progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0;
  const isDone = progress.done >= progress.total;
  return (
    <div className={`rounded-lg border px-3 py-2 ${
      isDone ? "border-success/40 bg-success-soft/40" : "border-brand-300 bg-brand-50/40"
    }`}>
      <div className="flex items-center gap-2 text-xs">
        {isDone ? (
          <Check className="h-3.5 w-3.5 text-success" />
        ) : (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-brand-500" />
        )}
        <span className="font-medium tabular-nums">
          {progress.done}/{progress.total} {t("common.files")}
        </span>
        <span className="text-muted truncate flex-1">
          {progress.current ?? (isDone ? t("jobs.completed") + " ✓" : "…")}
        </span>
        <span className="font-semibold tabular-nums text-brand-700">{pct}%</span>
      </div>
      <div className="h-1 mt-1.5 rounded-full bg-bg-soft overflow-hidden">
        <div
          className={`h-full transition-all ${isDone ? "bg-success" : "bg-brand-500"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

/**
 * Kompakt format satırı (Burak: "sayfa çok büyük, geniş kartlara gerek yok, ince
 * sağa doğru olsun"). İnce yatay satır: ikon + ad + kısa açıklama + indir.
 * Tıklayınca DİREKT indirir (doExport auto-download). Uyumlu araç rozetleri
 * kaldırıldı (sayfayı şişiriyordu); tam açıklama tooltip'te.
 */
function FormatCard({ f, busy, onClick }: { f: FormatInfo; busy: boolean; onClick: () => void }) {
  const t = useT();
  return (
    <button
      onClick={onClick}
      disabled={busy}
      title={f.info}
      className="group flex w-full flex-col gap-3 rounded-xl border border-border bg-white p-4 text-left transition hover:border-brand-500 hover:shadow-soft disabled:cursor-not-allowed disabled:opacity-50"
    >
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
          {f.icon}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-bold text-ink">{f.label}</span>
          <span className="mt-0.5 block text-[11px] leading-snug text-muted">{f.info}</span>
        </span>
        {busy ? (
          <Loader2 className="h-5 w-5 flex-shrink-0 animate-spin text-brand-500" />
        ) : (
          <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md bg-brand-50 text-brand-600 transition group-hover:bg-brand-500 group-hover:text-white">
            <Download className="h-4 w-4" />
          </span>
        )}
      </div>
      {/* Bu formatla çalışan araçlar — "nerede kullanılır" rehberi (Burak). */}
      <div className="space-y-1.5 border-t border-border/60 pt-2.5">
        <div className="text-[10px] font-semibold uppercase tracking-wide text-muted/70">
          {t("export.usedIn")}
        </div>
        <ToolBadgeList primary={f.primary} secondary={f.secondary} limit={8} />
      </div>
    </button>
  );
}
