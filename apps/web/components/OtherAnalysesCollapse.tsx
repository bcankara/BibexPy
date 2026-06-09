"use client";
import { useState } from "react";
import {
  Combine, Sparkles, Brain, Folder,
  ChevronDown, ChevronRight, Trash2, Loader2, History,
  AlertTriangle,
} from "lucide-react";
import { Card, CardBody } from "@/components/Card";
import { Button } from "@/components/Button";
import { api, formatBytes, type AnalysisItem, translateApiError} from "@/lib/api-client";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/cn";

type Props = {
  projectId: string;
  items: AnalysisItem[];           // sadece aktif olmayanlar
  busy?: boolean;
  onChanged: () => void;            // activate / delete sonrası refresh
};

// Analiz tonu hiyerarşisi (ActiveAnalysisHero ile aynı):
//   simple=slate / enhanced=brand-cyan / smart=emerald / unknown=slate-soft
const METHOD_META: Record<string, { label: string; icon: typeof Combine; tone: string; bg: string }> = {
  simple: { label: "Simple", icon: Combine, tone: "text-slate-700", bg: "bg-slate-50" },
  enhanced: { label: "Enhanced", icon: Sparkles, tone: "text-brand-700", bg: "bg-brand-50" },
  smart: { label: "Smart", icon: Brain, tone: "text-emerald-700", bg: "bg-emerald-50" },
  unknown: { label: "Legacy", icon: Folder, tone: "text-slate-500", bg: "bg-slate-50" },
};

function fmtDate(ts?: number | null): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleString(undefined, {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

export function OtherAnalysesCollapse({ projectId, items, busy, onChanged }: Props) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [acting, setActing] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (items.length === 0) return null;

  async function activate(id: string) {
    setActing(id);
    setError(null);
    try {
      await api.activateAnalysis(projectId, id);
      onChanged();
    } catch (e) {
      setError(translateApiError(t, e, "analyses.activationFailed"));
    } finally {
      setActing(null);
    }
  }

  async function remove(id: string) {
    setActing(id);
    setError(null);
    try {
      await api.deleteAnalysis(projectId, id);
      setConfirmDelete(null);
      onChanged();
    } catch (e) {
      setError(translateApiError(t, e, "analyses.deleteFailed"));
    } finally {
      setActing(null);
    }
  }

  return (
    <Card>
      <button
        onClick={() => setOpen(!open)}
        className="w-full px-5 py-3 flex items-center gap-3 text-left hover:bg-bg-soft transition-colors"
      >
        {open ? <ChevronDown className="h-4 w-4 text-muted" /> : <ChevronRight className="h-4 w-4 text-muted" />}
        <History className="h-4 w-4 text-muted" />
        <span className="font-medium text-sm flex-1">
          {t("analyses.otherAnalyses")} <span className="text-muted font-normal">({items.length})</span>
        </span>
        <span className="text-[11px] text-muted">
          {open ? t("analyses.hide") : t("analyses.show")}
        </span>
      </button>

      {open && (
        <CardBody className="pt-0 border-t border-border">
          {error && (
            <div className="mb-2 px-3 py-1.5 rounded-md bg-danger-soft border border-danger/30 text-xs text-red-700 flex items-center gap-2">
              <AlertTriangle className="h-3.5 w-3.5" /> {error}
            </div>
          )}
          <p className="text-[11px] text-muted mb-2">
            {t("analyses.otherAnalysesHint")}
          </p>
          <div className="space-y-2">
            {items.map((a) => {
              const mk = (((a.method as string) in METHOD_META ? a.method : "unknown")) as keyof typeof METHOD_META;
              const meta = METHOD_META[mk];
              const Icon = meta.icon;
              return (
                <div
                  key={a.id}
                  className="rounded-lg border border-border bg-white px-3 py-2.5 hover:border-border-strong"
                >
                  <div className="flex items-start gap-3">
                    <div className={cn("w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0", meta.bg)}>
                      <Icon className={cn("h-4 w-4", meta.tone)} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium text-ink truncate">{a.label}</span>
                        <span className={cn(
                          "text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded font-medium",
                          meta.bg, meta.tone,
                        )}>
                          {t(`analyses.method.${mk}`)}
                        </span>
                        {a.source === "legacy_migration" && (
                          <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded font-medium bg-slate-100 text-slate-600 flex items-center gap-0.5">
                            <History className="h-3 w-3" /> {t("analyses.legacy")}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-3 mt-1 text-[11px] text-muted">
                        <span>{fmtDate(a.created_at)}</span>
                        <span>·</span>
                        <span>{a.file_count} {t("common.files")}</span>
                        <span>·</span>
                        <span>{formatBytes(a.total_size)}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-1 flex-shrink-0">
                      <Button
                        variant="ghost"
                        className="h-7 text-xs px-2"
                        disabled={busy || acting === a.id}
                        onClick={() => activate(a.id)}
                      >
                        {acting === a.id ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
                        {t("analyses.activate")}
                      </Button>
                      {confirmDelete === a.id ? (
                        <div className="flex items-center gap-1">
                          <Button
                            variant="ghost"
                            className="h-7 text-xs px-2 text-danger hover:bg-danger-soft"
                            disabled={acting === a.id}
                            onClick={() => remove(a.id)}
                          >
                            {t("analyses.deleteConfirm")}
                          </Button>
                          <Button
                            variant="ghost"
                            className="h-7 text-xs px-2"
                            disabled={acting === a.id}
                            onClick={() => setConfirmDelete(null)}
                          >
                            {t("common.cancel")}
                          </Button>
                        </div>
                      ) : (
                        <button
                          className="h-7 w-7 inline-flex items-center justify-center rounded-md text-muted hover:text-danger hover:bg-danger-soft"
                          disabled={busy || acting === a.id}
                          onClick={() => setConfirmDelete(a.id)}
                          title={t("common.delete")}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </CardBody>
      )}
    </Card>
  );
}
