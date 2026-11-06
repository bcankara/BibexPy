"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  api, translateApiError, translateTitle,
  type AuditEntry, type AuditSummary, type QualityStats, type MethodologyReport,
} from "@/lib/api-client";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { Button } from "@/components/Button";
import { DataFlowDiagram } from "@/components/DataFlowDiagram";
import { PageHeader } from "@/components/PageHeader";
import {
  History, Download, FileText, Camera, RotateCcw, Upload, Combine, Sparkles,
  Brain, Trash2, Star, FolderPlus, ScrollText, Loader2, AlertTriangle, Wand2,
} from "lucide-react";
import { useI18n, useT } from "@/lib/i18n";
import { formatDate, formatDateTime, formatNumber, formatPercent } from "@/lib/i18n/format";
import { auditKindLabel, userActionLabel } from "@/lib/i18n/labels";
import { cn } from "@/lib/cn";
import { useConfirm, useToast } from "@/components/Dialogs";
import { useProjectId } from "@/lib/use-project-id";
import { useProjectBusy } from "@/lib/job-store";

const KIND_ICONS: Record<string, { icon: React.ReactNode; tone: string }> = {
  project_create:          { icon: <FolderPlus className="h-4 w-4" />, tone: "info" },
  upload:                  { icon: <Upload className="h-4 w-4" />,   tone: "info" },
  convert:                 { icon: <FileText className="h-4 w-4" />, tone: "info" },
  merge:                   { icon: <Combine className="h-4 w-4" />,  tone: "brand" },
  filter_save:             { icon: <Star className="h-4 w-4" />,     tone: "warning" },
  records_delete:          { icon: <Trash2 className="h-4 w-4" />,   tone: "danger" },
  enrich_api:              { icon: <Sparkles className="h-4 w-4" />, tone: "brand" },
  enrich_ml:               { icon: <Brain className="h-4 w-4" />,    tone: "brand" },
  enrich_selected_requested:{ icon: <Sparkles className="h-4 w-4" />, tone: "brand" },
  disambiguate:            { icon: <Sparkles className="h-4 w-4" />, tone: "accent" },
  snapshot:                { icon: <Camera className="h-4 w-4" />,   tone: "neutral" },
  snapshot_restore:        { icon: <RotateCcw className="h-4 w-4" />, tone: "warning" },
  export:                  { icon: <Download className="h-4 w-4" />, tone: "success" },
  report:                  { icon: <ScrollText className="h-4 w-4" />, tone: "neutral" },
  enrich_selected:         { icon: <Sparkles className="h-4 w-4" />, tone: "brand" },
  enrich_field:            { icon: <Sparkles className="h-4 w-4" />, tone: "brand" },
  records_edit:            { icon: <Wand2 className="h-4 w-4" />,    tone: "warning" },
  filter_apply:            { icon: <Star className="h-4 w-4" />,     tone: "warning" },
  format_migration:        { icon: <Camera className="h-4 w-4" />,   tone: "neutral" },
  disambiguate_authors:    { icon: <Sparkles className="h-4 w-4" />, tone: "accent" },
  disambiguate_affiliations:{ icon: <Sparkles className="h-4 w-4" />, tone: "accent" },
  merge_borderline:        { icon: <Combine className="h-4 w-4" />, tone: "warning" },
  analysis_activate:       { icon: <Combine className="h-4 w-4" />, tone: "brand" },
  analysis_delete:         { icon: <Trash2 className="h-4 w-4" />,   tone: "danger" },
};

const TONE_BG: Record<string, string> = {
  info: "bg-info-soft text-blue-700 border-info/30",
  brand: "bg-brand-50 text-brand-700 border-brand-200",
  warning: "bg-warning-soft text-amber-700 border-warning/30",
  danger: "bg-danger-soft text-red-700 border-danger/30",
  accent: "bg-brand-50 text-accent border-brand-200",
  success: "bg-success-soft text-emerald-700 border-success/30",
  neutral: "bg-bg-soft text-muted border-border",
};

export default function ReportPage() {
  const id = useProjectId();
  // An exclusive job of the project runs: restoring a snapshot would be refused (HTTP 409).
  const { busy: jobBusy } = useProjectBusy(id);
  const { t, locale } = useI18n();
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [summary, setSummary] = useState<AuditSummary | null>(null);
  const [stats, setStats] = useState<QualityStats | null>(null);
  const [loading, setLoading] = useState(true);
  // Which snapshot is being restored right now — that row's "Restore" button turns
  // into a spinner + gets locked (re-entry is prevented).
  const [restoringSnapshot, setRestoringSnapshot] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([
      api.listAudit(id, 1000),
      api.auditSummary(id),
      api.qualityStats(id).catch(() => null),
    ]).then(([e, s, q]) => {
      if (!alive) return;
      setEntries(e);
      setSummary(s);
      setStats(q);
      setLoading(false);
    }).catch(() => setLoading(false));
    return () => { alive = false; };
  }, [id]);

  const confirm = useConfirm();
  const toast = useToast();

  const reload = useCallback(async () => {
    const [e, s, q] = await Promise.all([
      api.listAudit(id, 1000),
      api.auditSummary(id),
      api.qualityStats(id).catch(() => null),
    ]);
    setEntries(e); setSummary(s); setStats(q);
  }, [id]);

  async function handleRestore(snapshot: string) {
    if (restoringSnapshot || jobBusy) return;
    const ok = await confirm({
      title: t("audit.restoreTitle"),
      message: t("audit.restoreConfirm"),
      confirmLabel: t("audit.restore"),
      tone: "danger",
    });
    if (!ok) return;
    setRestoringSnapshot(snapshot);
    try {
      const r = await api.restoreRecordSnapshot(id, snapshot);
      toast(t("audit.restoredToast", { n: r.restored }), { tone: "success" });
      await reload();
    } catch (err) {
      toast(translateApiError(t, err, "audit.restoreFailed"), { tone: "danger" });
    } finally {
      setRestoringSnapshot(null);
    }
  }

  if (loading) {
    return (
      <>
        <PageHeader title={t("report.title")} subtitle={t("report.subtitle")} />
        <div className="max-w-6xl mx-auto px-6 py-8 text-center text-muted">{t("common.loading")}</div>
      </>
    );
  }

  const firstDate = summary?.first_ts ? new Date(summary.first_ts * 1000) : null;
  const lastDate = summary?.last_ts ? new Date(summary.last_ts * 1000) : null;

  return (
    <>
      <PageHeader
        title={t("report.title")}
        subtitle={t("report.subtitle")}
      />
      <div className="max-w-6xl mx-auto px-6 py-6 space-y-4">

        {/* Summary cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <SummaryCard icon={<History className="h-4 w-4" />} label={t("report.totalOperations")}
            value={summary ? formatNumber(summary.total, locale) : "0"} />
          <SummaryCard icon={<FileText className="h-4 w-4" />} label={t("report.recordCount")}
            value={stats ? formatNumber(stats.total_records, locale) : "—"} />
          <SummaryCard icon={<Sparkles className="h-4 w-4" />} label={t("records.quality.healthScore")}
            value={stats ? formatPercent(Math.round(stats.health_score * 100), locale, 0) : "—"}
            tone={stats && stats.health_score >= 0.9 ? "success" : stats && stats.health_score >= 0.7 ? "warning" : "danger"} />
          <SummaryCard icon={<Camera className="h-4 w-4" />} label={t("report.firstLast")}
            value={firstDate && lastDate ? `${formatDate(firstDate, locale)} → ${formatDate(lastDate, locale)}` : "—"} small />
        </div>

        {/* Category distribution */}
        {summary && summary.total > 0 && (
          <Card>
            <CardHeader><h2 className="font-semibold text-sm">{t("report.categoryDist")}</h2></CardHeader>
            <CardBody>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
                {Object.entries(summary.by_kind).sort((a, b) => b[1] - a[1]).map(([kind, count]) => {
                  const meta = KIND_ICONS[kind === "enrich_selected_requested" ? "enrich_selected" : kind] ?? { icon: <FileText className="h-4 w-4" />, tone: "neutral" };
                  const label = auditKindLabel(t, kind);
                  return (
                    <div key={kind} className={cn("border rounded-lg px-3 py-2.5", TONE_BG[meta.tone])}>
                      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide opacity-80">
                        {meta.icon} {label}
                      </div>
                      <div className="text-xl font-bold mt-1 tabular-nums">{formatNumber(count, locale)}</div>
                    </div>
                  );
                })}
              </div>
            </CardBody>
          </Card>
        )}

        {/* Outputs — raw log + LLM manuscript report */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
          <RawLogsCard id={id} />
          <MethodologyCard projectId={id} hasOps={(summary?.total ?? 0) > 0} />
        </div>

        {/* Data flow diagram — automatic from the audit log; SVG/PNG downloadable */}
        <DataFlowDiagram projectId={id} />

        {/* Chronology — at the bottom */}
        <Card>
          <CardHeader>
            <h2 className="font-semibold text-sm flex-1">{t("report.chronological")}</h2>
            <span className="text-xs text-muted">{formatNumber(entries.length, locale)} {t("audit.operations")}</span>
          </CardHeader>
          <CardBody>
            {entries.length === 0 ? (
              <div className="text-center py-12">
                <History className="h-10 w-10 mx-auto text-muted/40 mb-3" />
                <p className="text-sm text-muted">{t("audit.noEntries")}</p>
                <p className="text-[11px] text-muted mt-1">{t("audit.subtitle")}</p>
              </div>
            ) : (
              <ol className="relative space-y-0">
                <div className="absolute left-[18px] top-2 bottom-2 w-px bg-border" />
                {entries.map((e, i) => (
                  <ReportEntry
                    key={i}
                    entry={e}
                    idx={entries.length - i}
                    onRestore={handleRestore}
                    restoring={!!e.snapshot && restoringSnapshot === e.snapshot}
                    locked={jobBusy}
                  />
                ))}
              </ol>
            )}
          </CardBody>
        </Card>
      </div>
    </>
  );
}

/** Raw change log card — prominent MD / TXT / PDF download. */
function RawLogsCard({ id }: { id: string }) {
  const { t, locale } = useI18n();
  const fmts: Array<"md" | "txt" | "pdf"> = ["md", "txt", "pdf"];
  return (
    <Card>
      <CardHeader>
        <ScrollText className="h-4 w-4 text-brand-600" />
        <h2 className="font-semibold text-sm">{t("report.rawLogs.title")}</h2>
      </CardHeader>
      <CardBody className="space-y-3">
        <p className="text-xs text-muted">{t("report.rawLogs.desc")}</p>
        <div className="flex items-center gap-2 flex-wrap">
          {fmts.map((f) => (
            <a key={f} href={api.reportLogUrl(id, f, locale)} target="_blank" rel="noreferrer"
              className="text-sm font-semibold px-3.5 py-2 rounded-lg border border-border bg-white hover:border-brand-400 hover:bg-brand-50 text-ink hover:text-brand-700 flex items-center gap-2 transition shadow-soft">
              <Download className="h-4 w-4" /> {f.toUpperCase()}
            </a>
          ))}
        </div>
      </CardBody>
    </Card>
  );
}

/** Short sample (representative) manuscript report — a preview while no report has been generated yet. */
const METHODOLOGY_SAMPLE = `## Data sources
A total of 1,204 bibliographic records were retrieved from Web of Science (n = 712) and Scopus (n = 492).

## Deduplication and merging
The two sources were merged following established record-linkage practice; after deduplication, 737 unique records remained.

## Screening and enrichment
Records published before 2013 were excluded, and missing DOIs and abstracts were completed by querying CrossRef and OpenAlex.

## Author disambiguation
Author name variants were normalized and homonymous names were separated. Data preparation was performed using BibexPy (Kara et al., 2025).`;

/** LLM-based manuscript report card (English). */
function MethodologyCard({ projectId, hasOps }: { projectId: string; hasOps: boolean }) {
  const { t, locale } = useI18n();
  const [report, setReport] = useState<MethodologyReport | null>(null);
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [model, setModel] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.getMethodology(projectId).then((r) => { if (alive) setReport(r?.text ? r : null); }).catch(() => {});
    api.disambiguateStatus(projectId)
      .then((s) => { if (alive) { setConfigured(s.configured); setModel(s.model || ""); } })
      .catch(() => { if (alive) setConfigured(false); });
    return () => { alive = false; };
  }, [projectId]);

  async function generate() {
    setBusy(true); setError(null);
    try {
      const r = await api.generateMethodology(projectId);
      setReport(r?.text ? r : null);
    } catch (e) {
      setError(translateApiError(t, e, "report.methodology.failed"));
    } finally { setBusy(false); }
  }

  const hasReport = !!report?.text;

  return (
    <Card className="border-brand-200">
      <CardHeader>
        <div className="flex items-center gap-2 flex-1">
          <Wand2 className="h-4 w-4 text-brand-600" />
          <h2 className="font-semibold text-sm">{t("report.methodology.title")}</h2>
        </div>
        {hasReport && (
          <div className="flex items-center gap-1">
            {(["md", "txt", "pdf"] as const).map((f) => (
              <a key={f} href={api.methodologyUrl(projectId, f)} target="_blank" rel="noreferrer"
                className="text-xs font-semibold px-2.5 py-1 rounded-md border border-border bg-white hover:border-brand-400 text-muted hover:text-brand-700 flex items-center gap-1.5 transition">
                <Download className="h-3.5 w-3.5" /> {f.toUpperCase()}
              </a>
            ))}
          </div>
        )}
      </CardHeader>
      <CardBody className="space-y-3">
        <p className="text-xs text-muted">{t("report.methodology.desc")}</p>

        {configured === false && (
          <div className="rounded-lg border border-warning/40 bg-warning-soft px-3 py-2 text-sm text-amber-900 flex gap-2">
            <AlertTriangle className="h-4 w-4 mt-0.5 flex-shrink-0" />
            <div>
              {t("report.methodology.notConfigured")}{" "}
              <Link href="/settings" className="underline font-medium">{t("report.methodology.settingsLink")}</Link>
            </div>
          </div>
        )}

        {error && <div className="rounded-lg border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-red-700">{error}</div>}

        <div className="flex items-center gap-2 flex-wrap">
          <Button onClick={generate} disabled={busy || configured === false || !hasOps} size="sm">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
            {hasReport ? t("report.methodology.regenerate") : t("report.methodology.generate")}
          </Button>
          {!hasOps && <span className="text-xs text-muted">{t("report.methodology.noOperations")}</span>}
          {busy && <span className="text-xs text-muted">{t("report.methodology.generating")}</span>}
          {configured && model && (
            <span className="text-[11px] text-muted ml-auto">
              {t("disambiguate.modelLabel")}: <code className="font-mono bg-bg-soft px-1 rounded">{model}</code>
            </span>
          )}
        </div>

        {!hasReport && !busy && (
          <div className="rounded-lg border border-dashed border-border bg-bg-soft/40 px-4 py-3">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-muted mb-1.5">{t("report.methodology.sampleLabel")}</p>
            <pre className="whitespace-pre-wrap font-sans text-[12px] leading-relaxed text-muted/80">{METHODOLOGY_SAMPLE}</pre>
          </div>
        )}

        {hasReport && (
          <div className="space-y-2">
            <div className="rounded-lg border border-border bg-white px-4 py-3 max-h-[28rem] overflow-y-auto">
              <pre className="whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-ink">{report!.text}</pre>
            </div>
            <p className="text-[11px] text-muted flex items-center gap-1.5">
              <Sparkles className="h-3 w-3" />
              {t("report.methodology.draftNote")}
              {report?.generated_at && (
                <span className="ml-auto">
                  {t("report.methodology.generatedAt")}: {formatDateTime(new Date(report.generated_at * 1000), locale)}
                </span>
              )}
            </p>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function SummaryCard({ icon, label, value, tone = "brand", small }: {
  icon: React.ReactNode; label: string; value: string;
  tone?: "brand" | "success" | "warning" | "danger"; small?: boolean;
}) {
  return (
    <div className={cn("border rounded-xl px-4 py-3", TONE_BG[tone])}>
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide opacity-80">
        {icon} {label}
      </div>
      <div className={cn("font-bold mt-1.5 tabular-nums", small ? "text-sm" : "text-2xl")}>{value}</div>
    </div>
  );
}

function ReportEntry({ entry, idx, onRestore, restoring, locked = false }: {
  entry: AuditEntry;
  idx: number;
  onRestore: (snapshot: string) => void;
  restoring?: boolean;
  /** A job holds the project: restoring a snapshot is disabled. */
  locked?: boolean;
}) {
  const { t, locale } = useI18n();
  const meta = KIND_ICONS[entry.kind === "enrich_selected_requested" ? "enrich_selected" : entry.kind] ?? { icon: <FileText className="h-4 w-4" />, tone: "neutral" };
  const label = auditKindLabel(t, entry.kind);
  const dateStr = formatDateTime(new Date(entry.ts * 1000), locale);

  return (
    <li className="relative pl-10 py-3">
      <div className={cn("absolute left-2 top-3.5 w-8 h-8 rounded-full flex items-center justify-center border-2", TONE_BG[meta.tone])}>
        {meta.icon}
      </div>
      <div className="bg-bg-soft/50 rounded-lg px-3 py-2.5">
        <div className="flex items-baseline gap-2">
          <span className={cn("text-[10px] font-semibold uppercase tracking-wide", TONE_BG[meta.tone].split(" ")[1])}>
            #{formatNumber(idx, locale)} · {label}
          </span>
          <span className="text-[10px] text-muted ml-auto">{dateStr}</span>
        </div>
        <p className="text-sm font-medium mt-1 text-ink">{translateTitle(t, entry)}</p>
        {entry.details && Object.keys(entry.details).length > 0 && (
          <details className="mt-1.5">
            <summary className="text-[11px] text-brand-600 hover:underline cursor-pointer">{t("audit.showDetails")}</summary>
            <pre className="mt-1 bg-white border border-border rounded p-2 text-[10px] overflow-x-auto text-muted whitespace-pre-wrap">
              {JSON.stringify(entry.details, null, 2)}
            </pre>
          </details>
        )}
        <div className="flex items-center gap-3 mt-1.5">
          {entry.snapshot && (
            <>
              <span className="text-[10px] text-muted font-mono flex items-center gap-1">
                <Camera className="h-3 w-3" /> {entry.snapshot.split(/[\\/]/).pop()}
              </span>
              <button
                onClick={() => onRestore(entry.snapshot!)}
                disabled={restoring || locked}
                className="text-[10px] font-semibold text-amber-700 hover:text-amber-900 flex items-center gap-1 px-2 py-0.5 rounded-md border border-warning/40 bg-warning-soft/60 hover:bg-warning-soft transition disabled:opacity-60 disabled:cursor-not-allowed"
                title={locked ? t("lock.busyReason") : t("audit.restoreTitle")}
              >
                {restoring ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
                {t("audit.restore")}
              </button>
            </>
          )}
          {entry.user_action && (
            <span className="text-[10px] text-muted italic">{userActionLabel(t, entry.user_action)}</span>
          )}
        </div>
      </div>
    </li>
  );
}
