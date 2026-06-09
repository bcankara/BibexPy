"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  X, FileText, History, ExternalLink, RotateCcw, Download,
  Upload, Combine, Sparkles, Brain, Trash2, Save, Star, Camera,
  Layers, Database, Check, AlertCircle,
} from "lucide-react";
import { api, translateTitle, translateApiError, type AuditEntry } from "@/lib/api-client";
import { useT } from "@/lib/i18n";
import { useConfirm, useToast } from "./Dialogs";
import { cn } from "@/lib/cn";

// Locale-aware label resolver — runtime'da t() ile çevrilir
const KIND_ICONS: Record<string, { icon: React.ReactNode; color: string; labelKey: string }> = {
  upload:                  { icon: <Upload className="h-3.5 w-3.5" />,  color: "text-info",      labelKey: "audit.kinds.upload" },
  convert:                 { icon: <FileText className="h-3.5 w-3.5" />, color: "text-info",      labelKey: "audit.kinds.convert" },
  merge:                   { icon: <Combine className="h-3.5 w-3.5" />,  color: "text-brand-500", labelKey: "audit.kinds.merge" },
  filter_save:             { icon: <Star className="h-3.5 w-3.5" />,     color: "text-warning",   labelKey: "audit.kinds.filter_save" },
  records_delete:          { icon: <Trash2 className="h-3.5 w-3.5" />,   color: "text-danger",    labelKey: "audit.kinds.records_delete" },
  enrich_api:              { icon: <Sparkles className="h-3.5 w-3.5" />, color: "text-brand-500", labelKey: "audit.kinds.enrich_api" },
  enrich_ml:               { icon: <Brain className="h-3.5 w-3.5" />,    color: "text-brand-500", labelKey: "audit.kinds.enrich_ml" },
  enrich_selected_requested:{ icon: <Sparkles className="h-3.5 w-3.5" />, color: "text-brand-500", labelKey: "audit.kinds.enrich_selected" },
  disambiguate:            { icon: <Sparkles className="h-3.5 w-3.5" />, color: "text-accent",    labelKey: "audit.kinds.disambiguate" },
  snapshot:                { icon: <Camera className="h-3.5 w-3.5" />,   color: "text-muted",     labelKey: "audit.kinds.snapshot" },
  snapshot_restore:        { icon: <RotateCcw className="h-3.5 w-3.5" />, color: "text-warning",  labelKey: "audit.kinds.snapshot_restore" },
  export:                  { icon: <Download className="h-3.5 w-3.5" />, color: "text-success",   labelKey: "audit.kinds.export" },
  analysis_activate:       { icon: <Combine className="h-3.5 w-3.5" />,  color: "text-brand-500", labelKey: "audit.kinds.analysis_activate" },
  analysis_delete:         { icon: <Trash2 className="h-3.5 w-3.5" />,   color: "text-danger",    labelKey: "audit.kinds.analysis_delete" },
  merge_borderline:        { icon: <Layers className="h-3.5 w-3.5" />,   color: "text-warning",   labelKey: "audit.kinds.merge_borderline" },
};

type Props = {
  projectId: string;
  open: boolean;
  onClose: () => void;
};

export function AuditLogPanel({ projectId, open, onClose }: Props) {
  const t = useT();
  const confirm = useConfirm();
  const toast = useToast();
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [mounted, setMounted] = useState(false);
  // onClose'u ref'te tut — parent her render'da yeni closure verse de efekt yeniden
  // çalışıp listeyi tekrar çekmesin / animasyonu sıfırlamasın.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) {
      setMounted(false);
      return;
    }
    requestAnimationFrame(() => setMounted(true));
    setLoading(true);
    api.listAudit(projectId, 200)
      .then((d) => setEntries(d.reverse()))
      .catch(() => setEntries([]))
      .finally(() => setLoading(false));
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCloseRef.current(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, projectId]);

  async function refresh() {
    setLoading(true);
    try {
      const d = await api.listAudit(projectId, 200);
      setEntries(d.reverse());
    } finally { setLoading(false); }
  }

  async function handleRestore(snapshot: string) {
    const ok = await confirm({
      title: t("audit.restoreTitle"),
      message: t("audit.restoreConfirm"),
      confirmLabel: t("audit.restore"),
      tone: "danger",
    });
    if (!ok) return;
    try {
      const r = await api.restoreRecordSnapshot(projectId, snapshot);
      toast(t("audit.restoredToast", { n: r.restored }), { tone: "success" });
      await refresh();
    } catch (e) {
      toast(translateApiError(t, e, "audit.restoreFailed"), { tone: "danger" });
    }
  }

  async function clearAll() {
    if (!(await confirm({ message: t("audit.clearConfirm"), tone: "danger" }))) return;
    try {
      await api.clearAudit(projectId);
      await refresh();
    } catch {}
  }

  if (!open) return null;

  return (
    <>
      <div
        onClick={onClose}
        className={cn("fixed inset-0 bg-ink/30 backdrop-blur-sm z-40 transition-opacity", mounted ? "opacity-100" : "opacity-0")}
      />
      <div
        className={cn(
          "fixed top-0 right-0 h-full w-full max-w-xl bg-white shadow-2xl z-50 flex flex-col",
          "transition-transform duration-200 ease-out",
          mounted ? "translate-x-0" : "translate-x-full",
        )}
      >
        <div className="px-5 py-3.5 border-b border-border flex items-center gap-3">
          <History className="h-4 w-4 text-brand-500" />
          <div className="flex-1">
            <h2 className="font-semibold text-sm">{t("audit.title")}</h2>
            <p className="text-[11px] text-muted">{t("audit.totalOperations", { n: entries.length })}</p>
          </div>
          <Link
            href={`/projects/${projectId}/report`}
            className="text-xs text-brand-600 hover:underline flex items-center gap-1"
          >
            {t("nav.report")} <ExternalLink className="h-3 w-3" />
          </Link>
          <a
            href={api.auditReportUrl(projectId)}
            target="_blank" rel="noreferrer"
            className="text-xs text-muted hover:text-brand-600 flex items-center gap-1"
            title={t("audit.downloadMarkdown")}
          >
            <Download className="h-3 w-3" />
          </a>
          <button onClick={onClose} className="p-1.5 rounded-md hover:bg-bg-soft text-muted hover:text-ink">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <p className="text-center py-12 text-muted text-sm">{t("common.loading")}</p>
          ) : entries.length === 0 ? (
            <div className="text-center py-12 px-6">
              <History className="h-8 w-8 mx-auto text-muted/40 mb-3" />
              <p className="text-sm text-muted">{t("audit.noEntries")}</p>
              <p className="text-[11px] text-muted mt-1">
                {t("audit.subtitle")}
              </p>
            </div>
          ) : (
            <ol className="relative">
              {entries.map((e, i) => (
                <AuditEntryRow key={i} entry={e} onRestore={handleRestore} />
              ))}
            </ol>
          )}
        </div>

        {entries.length > 0 && (
          <div className="px-5 py-3 border-t border-border flex items-center justify-between">
            <button
              onClick={clearAll}
              className="text-[11px] text-muted hover:text-danger flex items-center gap-1"
            >
              <Trash2 className="h-3 w-3" /> {t("audit.clearAll")}
            </button>
          </div>
        )}
      </div>
    </>
  );
}

function SmartMergeSummary({ details }: { details: Record<string, unknown> }) {
  const t = useT();
  const d = details as Record<string, number | string | Record<string, number> | string[] | undefined>;
  const wos = Number(d.wos_input ?? 0);
  const scp = Number(d.scopus_input ?? 0);
  const merged = Number(d.merged_count ?? 0);
  const matched = Number(d.matched_pairs ?? 0);
  const borderline = Number(d.borderline_count ?? 0);
  const borderlinePending = Number(d.borderline_pending ?? 0);
  const conflict = Number(d.conflict_count ?? 0);
  const lostWos = Number(d.lost_wos_count ?? 0);
  const lostScp = Number(d.lost_scopus_count ?? 0);
  const stages: Record<string, number> = (d.match_stages as Record<string, number>) || {};
  const fieldDist: Record<string, number> = (d.field_source_distribution as Record<string, number>) || {};
  const durationSec = d.duration_seconds != null ? Number(d.duration_seconds) : null;
  const outputFiles = Array.isArray(d.output_files) ? (d.output_files as string[]) : [];
  const totalInput = wos + scp;
  const dedupRate = totalInput > 0 ? ((totalInput - merged) / totalInput * 100) : 0;

  return (
    <div className="mt-2 space-y-2">
      {/* Akış özeti — büyük sayı kartları */}
      <div className="grid grid-cols-4 gap-1.5">
        <SummaryCell label={t("audit.smart.rawInput")} value={`${totalInput}`} sub={`${wos} + ${scp}`} tone="muted" />
        <SummaryCell label={t("audit.smart.matched")} value={`${matched}`} sub={`${dedupRate.toFixed(1)}% ${t("audit.smart.dedupShort")}`} tone="brand" />
        <SummaryCell label={t("audit.smart.borderline")} value={`${borderlinePending}/${borderline}`} sub={t("audit.smart.pendingTotal")} tone={borderlinePending > 0 ? "warning" : "muted"} />
        <SummaryCell label={t("audit.smart.output")} value={`${merged}`} sub={`${lostWos}+${lostScp} ${t("audit.smart.lostShort")}`} tone="success" />
      </div>

      {/* Stage dağılımı */}
      {Object.keys(stages).length > 0 && (
        <div className="rounded-md border border-border bg-bg-soft/40 px-2 py-1.5">
          <div className="text-[9px] font-semibold uppercase tracking-wider text-muted mb-1 flex items-center gap-1">
            <Layers className="h-2.5 w-2.5" /> {t("merge.smart.matchStagesHeader")}
          </div>
          <div className="flex flex-wrap gap-1">
            {Object.entries(stages).map(([k, v]) => (
              <span key={k} className="text-[10px] px-1.5 py-0.5 rounded bg-white border border-border">
                <span className="font-mono text-muted">{k}:</span> <strong>{String(v)}</strong>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Field source distribution */}
      {Object.keys(fieldDist).length > 0 && (
        <div className="rounded-md border border-border bg-bg-soft/40 px-2 py-1.5">
          <div className="text-[9px] font-semibold uppercase tracking-wider text-muted mb-1 flex items-center gap-1">
            <Database className="h-2.5 w-2.5" /> {t("audit.smart.conflictResolution", { conflict })}
          </div>
          <div className="flex flex-wrap gap-1">
            {Object.entries(fieldDist).map(([src, v]) => {
              const total = Object.values(fieldDist).reduce((s, n) => s + (n as number), 0);
              const pct = total > 0 ? ((v as number) / total * 100).toFixed(0) : "0";
              return (
                <span key={src} className="text-[10px] px-1.5 py-0.5 rounded bg-white border border-border">
                  <span className="font-mono text-muted">{src}:</span> <strong>{String(v)}</strong> <span className="text-muted">({pct}%)</span>
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* Süre + status */}
      {(durationSec != null || outputFiles.length > 0) && (
        <div className="flex items-center gap-2 text-[10px] text-muted">
          {durationSec != null && (
            <span>⏱ {durationSec.toFixed(1)} {t("audit.smart.seconds")}</span>
          )}
          {outputFiles.length > 0 && (
            <span>📦 {t("audit.smart.filesProduced", { n: outputFiles.length })}</span>
          )}
        </div>
      )}
    </div>
  );
}

function SummaryCell({ label, value, sub, tone }: {
  label: string; value: string; sub?: string;
  tone: "muted" | "brand" | "success" | "warning";
}) {
  const toneMap = {
    muted: "bg-bg-soft text-ink border-border",
    brand: "bg-brand-50 text-brand-700 border-brand-200",
    success: "bg-success-soft text-emerald-700 border-success/30",
    warning: "bg-warning-soft text-amber-700 border-warning/30",
  };
  return (
    <div className={cn("rounded border px-2 py-1", toneMap[tone])}>
      <div className="text-[9px] font-semibold uppercase tracking-wider opacity-75">{label}</div>
      <div className="text-sm font-bold tabular-nums leading-tight">{value}</div>
      {sub && <div className="text-[9px] opacity-70">{sub}</div>}
    </div>
  );
}


function AuditEntryRow({ entry, onRestore }: { entry: AuditEntry; onRestore: (snapshot: string) => void }) {
  const t = useT();
  const iconMeta = KIND_ICONS[entry.kind] ?? { icon: <FileText className="h-3.5 w-3.5" />, color: "text-muted", labelKey: "" };
  const label = iconMeta.labelKey ? t(iconMeta.labelKey) : entry.kind;
  const date = new Date(entry.ts * 1000);
  const dateStr = date.toLocaleString(undefined, { dateStyle: "short", timeStyle: "medium" });
  const [open, setOpen] = useState(false);

  const beforeAfter = (entry.before || entry.after);
  const isSmartMerge = entry.kind === "merge" && (entry.details as Record<string, unknown> | undefined)?.method === "smart";

  return (
    <li className="px-5 py-3 border-b border-border hover:bg-bg-soft/50">
      <div className="flex items-start gap-3">
        <div className={cn("mt-0.5 flex-shrink-0", iconMeta.color)}>{iconMeta.icon}</div>
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2">
            <span className={cn("text-[10px] font-semibold uppercase tracking-wide", iconMeta.color)}>
              {label}
            </span>
            {isSmartMerge && (
              <span className="text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-success-soft text-emerald-700 border border-success/30">
                Smart
              </span>
            )}
            <span className="text-[10px] text-muted ml-auto">{dateStr}</span>
          </div>
          <p className="text-sm text-ink font-medium mt-0.5">{translateTitle(t, entry)}</p>

          {/* Smart Merge için zengin özet — JSON dump değil */}
          {isSmartMerge && entry.details && (
            <SmartMergeSummary details={entry.details as Record<string, unknown>} />
          )}

          {beforeAfter && (
            <div className="mt-1 text-[11px] text-muted flex gap-2 items-center">
              {entry.before && <span>{t("audit.before")}: <code className="text-ink">{JSON.stringify(entry.before)}</code></span>}
              {entry.after && <span>→ {t("audit.after")}: <code className="text-ink">{JSON.stringify(entry.after)}</code></span>}
            </div>
          )}
          {entry.details && Object.keys(entry.details).length > 0 && !isSmartMerge && (
            <>
              <button
                onClick={() => setOpen(!open)}
                className="text-[11px] text-brand-600 hover:underline mt-1"
              >
                {open ? t("audit.hideDetails") : t("audit.showDetails")}
              </button>
              {open && (
                <pre className="text-[10px] mt-1 bg-bg-soft rounded p-2 overflow-x-auto text-muted">
                  {JSON.stringify(entry.details, null, 2)}
                </pre>
              )}
            </>
          )}
          {entry.snapshot && (
            <div className="mt-1.5 flex items-center gap-2 flex-wrap">
              <span className="text-[10px] text-muted font-mono flex items-center gap-1">
                <Camera className="h-3 w-3" /> {entry.snapshot.split(/[\\/]/).pop()}
              </span>
              <button
                onClick={() => onRestore(entry.snapshot!)}
                className="text-[10px] font-semibold text-amber-700 hover:text-amber-900 flex items-center gap-1 px-2 py-0.5 rounded-md border border-warning/40 bg-warning-soft/60 hover:bg-warning-soft transition"
                title={t("audit.restoreTitle")}
              >
                <RotateCcw className="h-3 w-3" /> {t("audit.restore")}
              </button>
            </div>
          )}
        </div>
      </div>
    </li>
  );
}
