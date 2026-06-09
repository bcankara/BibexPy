"use client";
import { useState } from "react";
import {
  Brain, Sparkles, Combine, Folder,
  Plus, Trash2, CheckCircle2,
  Loader2, MoreHorizontal, Copy, FileText,
} from "lucide-react";
import { Card, CardBody } from "@/components/Card";
import { Button } from "@/components/Button";
import { useToast } from "@/components/Dialogs";
import { api, formatBytes, type MergeSummary, type AnalysisItem, translateApiError} from "@/lib/api-client";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/cn";

type Props = {
  projectId: string;
  summary: MergeSummary;
  analysis: AnalysisItem;
  onNewAnalysis: () => void;
  onDeleted: () => void;
};

// Analiz tonu hiyerarşisi (V2 cyan + navy paletine uyumlu — marka tutarlılığı):
//   simple   = slate (nötr, basit birleştirme)
//   enhanced = cyan/brand (gelişmiş)
//   smart    = cyan/brand (AI premium — marka rengi, eskiden emerald idi)
//   unknown  = slate (legacy/eski)
const METHOD_META: Record<string, { label: string; icon: typeof Combine; tone: string; iconBg: string; ring: string }> = {
  simple: { label: "Simple", icon: Combine, tone: "text-slate-700", iconBg: "bg-slate-100", ring: "ring-slate-200" },
  enhanced: { label: "Enhanced", icon: Sparkles, tone: "text-brand-700", iconBg: "bg-brand-50", ring: "ring-brand-200" },
  smart: { label: "Smart", icon: Brain, tone: "text-brand-700", iconBg: "bg-brand-100", ring: "ring-brand-200" },
  unknown: { label: "Legacy", icon: Folder, tone: "text-slate-500", iconBg: "bg-slate-50", ring: "ring-slate-200" },
};

function fmtDate(ts?: number | null): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleString(undefined, {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

export function ActiveAnalysisHero({ projectId, summary, analysis, onNewAnalysis, onDeleted }: Props) {
  const t = useT();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const method = (analysis.method as keyof typeof METHOD_META) ?? "unknown";
  const methodKey = (((method as string) in METHOD_META ? method : "unknown")) as keyof typeof METHOD_META;
  const meta = METHOD_META[methodKey];
  const Icon = meta.icon;

  const stats = summary.general;

  async function handleDelete() {
    setDeleting(true);
    setError(null);
    try {
      await api.deleteAnalysis(projectId, analysis.id);
      setConfirmDelete(false);
      onDeleted();
    } catch (e) {
      setError(translateApiError(t, e, "analyses.deleteFailed"));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <Card className={cn("ring-2", meta.ring)}>
      <CardBody className="p-5">
        {error && (
          <div className="mb-3 px-3 py-1.5 rounded-md bg-danger-soft border border-danger/30 text-xs text-red-700">
            {error}
          </div>
        )}

        <div className="flex items-start gap-4 flex-wrap">
          {/* Icon */}
          <div className={cn("w-14 h-14 rounded-xl flex items-center justify-center flex-shrink-0", meta.iconBg)}>
            <Icon className={cn("h-7 w-7", meta.tone)} />
          </div>

          {/* Title + meta */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs uppercase tracking-wider font-bold text-brand-700 inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-brand-50 border border-brand-200">
                <CheckCircle2 className="h-3 w-3" /> {t("analyses.activeBadge")}
              </span>
              <span className={cn("text-xs font-bold uppercase tracking-wider px-1.5 py-0.5 rounded", meta.iconBg, meta.tone)}>
                {t(`analyses.method.${methodKey}`)}
              </span>
            </div>
            <h2 className="text-lg font-bold text-ink mt-1.5 truncate" title={analysis.label}>
              {analysis.label}
            </h2>
            <div className="text-[11px] text-muted mt-0.5 font-mono truncate" title={analysis.id}>
              {analysis.id} · {fmtDate(analysis.created_at)} · {analysis.file_count} {t("common.files")} · {formatBytes(analysis.total_size)}
            </div>
          </div>

          {/* CTA */}
          <div className="flex items-center gap-2 flex-shrink-0">
            <Button variant="secondary" onClick={onNewAnalysis} className="flex items-center gap-1.5">
              <Plus className="h-4 w-4" /> {t("analyses.newAnalysis")}
            </Button>
            <DeleteMenu
              confirmDelete={confirmDelete}
              setConfirmDelete={setConfirmDelete}
              deleting={deleting}
              onDelete={handleDelete}
              t={t}
            />
          </div>
        </div>

        {/* Mini stat strip */}
        {stats && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-4">
            <MiniStat label={t("analyses.statRecords")} value={stats.total_records.toLocaleString()} />
            <MiniStat label="WoS" value={stats.wos_records.toLocaleString()} />
            <MiniStat label="Scopus" value={stats.scopus_records.toLocaleString()} />
            <MiniStat
              label={t("analyses.statDedup")}
              value={`${(stats.dedup_rate * 100).toFixed(1)}%`}
              sub={`${stats.duplicates_removed.toLocaleString()} ${t("analyses.statDuplicates")}`}
            />
          </div>
        )}

        {/* #4: yayına-hazır yöntem paragrafı (kopyalanabilir) */}
        {stats && <MergeNarrative general={stats} createdAt={analysis.created_at} />}
      </CardBody>
    </Card>
  );
}

/** Smart Merge sonuçlarını yayına-hazır bir metin paragrafına dönüştürür (kopyalanabilir).
 * Tüm sayılar summary.general'dan; ek backend çağrısı yok. */
function MergeNarrative({
  general, createdAt,
}: {
  general: NonNullable<MergeSummary["general"]>;
  createdAt?: number | null;
}) {
  const t = useT();
  const toast = useToast();
  const dateStr = createdAt
    ? new Date(createdAt * 1000).toLocaleDateString(undefined, { day: "2-digit", month: "long", year: "numeric" })
    : "—";
  const totalInput = general.total_input ?? (general.wos_records + general.scopus_records);
  const text = t("merge.narrative.template", {
    date: dateStr,
    scopus: general.scopus_records.toLocaleString(),
    wos: general.wos_records.toLocaleString(),
    total_input: totalInput.toLocaleString(),
    duplicates: general.duplicates_removed.toLocaleString(),
    total: general.total_records.toLocaleString(),
  });

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      toast(t("merge.narrative.copied"), { tone: "success" });
    } catch { /* pano erişimi yoksa sessiz */ }
  }

  return (
    <div className="mt-4 rounded-lg border border-border bg-bg-soft/40 p-3">
      <div className="flex items-center gap-2 mb-1.5">
        <FileText className="h-3.5 w-3.5 text-brand-600 flex-shrink-0" />
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted flex-1">
          {t("merge.narrative.title")}
        </span>
        <button onClick={copy} className="text-xs flex items-center gap-1 text-brand-600 hover:text-brand-700 flex-shrink-0">
          <Copy className="h-3.5 w-3.5" /> {t("merge.narrative.copy")}
        </button>
      </div>
      <p className="text-xs text-ink leading-relaxed">{text}</p>
      <p className="text-[10px] text-muted mt-1.5">{t("merge.narrative.hint")}</p>
    </div>
  );
}

function MiniStat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-border bg-bg-soft px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-muted font-semibold">{label}</div>
      <div className="text-lg font-bold text-ink tabular-nums">{value}</div>
      {sub && <div className="text-[10px] text-muted tabular-nums">{sub}</div>}
    </div>
  );
}

function DeleteMenu({
  confirmDelete, setConfirmDelete, deleting, onDelete, t,
}: {
  confirmDelete: boolean;
  setConfirmDelete: (b: boolean) => void;
  deleting: boolean;
  onDelete: () => void;
  t: (k: string) => string;
}) {
  if (confirmDelete) {
    return (
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          className="h-9 text-xs text-danger hover:bg-danger-soft px-2.5"
          disabled={deleting}
          onClick={onDelete}
        >
          {deleting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
          {t("analyses.deleteConfirm")}
        </Button>
        <Button
          variant="ghost"
          className="h-9 text-xs px-2.5"
          disabled={deleting}
          onClick={() => setConfirmDelete(false)}
        >
          {t("common.cancel")}
        </Button>
      </div>
    );
  }
  return (
    <button
      onClick={() => setConfirmDelete(true)}
      className="h-9 w-9 inline-flex items-center justify-center rounded-md text-muted hover:text-danger hover:bg-danger-soft"
      title={t("common.delete")}
    >
      <MoreHorizontal className="h-4 w-4" />
    </button>
  );
}
