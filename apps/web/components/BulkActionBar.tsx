"use client";
import { useState } from "react";
import { Trash2, X, AlertTriangle, Loader2 } from "lucide-react";
import { Button } from "./Button";
import { api, translateApiError } from "@/lib/api-client";
import { useT } from "@/lib/i18n";
import { useToast } from "./Dialogs";
import { cn } from "@/lib/cn";

type Props = {
  projectId: string;
  selected: Set<string>;
  onClear: () => void;
  onChanged: () => void;
};

/**
 * Multi-select bulk action bar — DELETE only.
 * (The API-based "fill selected" was removed; filling is now the single "Fill all"
 * flow in Harmonization. Individual editing is done with inline edit.)
 */
export function BulkActionBar({ projectId, selected, onClear, onChanged }: Props) {
  const t = useT();
  const toast = useToast();
  const count = selected.size;
  const [confirm, setConfirm] = useState<null | "delete">(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (count === 0) return null;

  async function doDelete() {
    setBusy(true); setError(null);
    try {
      // The selection set holds record UIDs only (RecordsTable selects by UID).
      const uids = Array.from(selected);
      if (uids.length === 0) {
        setError(t("bulk.noSelected"));
        return;
      }
      const r = await api.deleteRecords(projectId, { uids, reason: reason || undefined });
      const notFound = r.not_found ?? 0;
      toast(
        t("bulk.deletedToast", { deleted: r.deleted, kept: r.kept }) +
        (notFound > 0 ? `\n${t("bulk.notFoundSkipped", { n: notFound })}` : "") +
        (r.snapshot ? `\n${t("disambiguate.snapshot")}: ${r.snapshot}` : ""),
        { tone: "success" },
      );
      onClear();
      setConfirm(null);
      setReason("");
      onChanged();
    } catch (e) {
      setError(translateApiError(t, e, "bulk.deleteFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-30">
      <div className={cn(
        "rounded-2xl bg-ink text-white shadow-2xl border border-ink-soft px-4 py-3",
        "flex items-center gap-3 backdrop-blur-md min-w-[420px] max-w-[680px]",
      )}>
        <div className="flex items-center gap-2">
          <span className="w-8 h-8 rounded-lg bg-brand-500 flex items-center justify-center font-bold text-sm tabular-nums">
            {count}
          </span>
          <span className="text-sm font-medium">{t("bulk.recordsSelected")}</span>
        </div>

        <div className="h-6 w-px bg-white/20" />

        {confirm === null ? (
          <>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setConfirm("delete")}
              className="bg-danger/30 hover:bg-danger/50 text-white border-0"
            >
              <Trash2 className="h-3.5 w-3.5" />
              {t("common.delete")}
            </Button>
            <button
              onClick={onClear}
              className="ml-auto text-xs text-white/60 hover:text-white flex items-center gap-1"
            >
              <X className="h-3 w-3" /> {t("bulk.clearSelection")}
            </button>
          </>
        ) : (
          <>
            <div className="flex-1 flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-warning flex-shrink-0" />
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder={t("bulk.reasonPlaceholder")}
                className="flex-1 bg-white/10 border border-white/20 rounded px-2 py-1 text-xs text-white placeholder:text-white/40 focus:outline-none focus:border-brand-400"
              />
            </div>
            <Button size="sm" variant="secondary" onClick={() => setConfirm(null)} disabled={busy} className="bg-white/10 text-white border-0">
              {t("common.cancel")}
            </Button>
            <Button size="sm" onClick={doDelete} disabled={busy} className="bg-danger hover:bg-danger/80 text-white">
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
              {t("common.delete")} ({count})
            </Button>
          </>
        )}
      </div>
      {error && (
        <div className="mt-2 rounded-lg border border-danger bg-danger/90 text-white px-3 py-2 text-xs text-center">
          {error}
        </div>
      )}
    </div>
  );
}
