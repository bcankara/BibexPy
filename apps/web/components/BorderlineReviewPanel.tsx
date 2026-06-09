"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type BorderlinePair, translateApiError} from "@/lib/api-client";
import { Card, CardBody, CardHeader } from "./Card";
import { Button } from "./Button";
import {
  AlertTriangle, Check, X, SkipForward, CheckSquare, RefreshCw, Loader2,
  Sparkles, ExternalLink, ShieldCheck, ChevronDown, ChevronRight, ListChecks,
} from "lucide-react";
import { useT } from "@/lib/i18n";
import { useConfirm, useToast } from "./Dialogs";
import { InfoTip } from "./InfoTip";
import { cn } from "@/lib/cn";


type Decision = "accept" | "reject" | "skip";


export function BorderlineReviewPanel({
  projectId, onApplied,
}: {
  projectId: string;
  onApplied?: () => void;
}) {
  const t = useT();
  const confirm = useConfirm();
  const toast = useToast();
  const [items, setItems] = useState<BorderlinePair[]>([]);
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [threshold, setThreshold] = useState(0.88);
  const [statusFilter, setStatusFilter] = useState<"pending" | "all">("pending");
  // #2: panel varsayılan KAPALI — ana kayıt listesi önde kalsın; isteyen açıp inceler.
  const [open, setOpen] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await api.listBorderline(projectId);
      setItems(list);
      setDecisions({});
    } catch (e) {
      setError(translateApiError(t, e, "borderline.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { refresh(); }, [refresh]);

  const visible = useMemo(() => {
    if (statusFilter === "pending") return items.filter((i) => i.status === "pending");
    return items;
  }, [items, statusFilter]);

  const counts = useMemo(() => {
    return {
      pending: items.filter((i) => i.status === "pending").length,
      accept: items.filter((i) => i.status === "accept").length,
      reject: items.filter((i) => i.status === "reject").length,
      skip: items.filter((i) => i.status === "skip").length,
      decided: Object.keys(decisions).length,
    };
  }, [items, decisions]);

  function setDecision(pair_id: string, d: Decision) {
    setDecisions((prev) => {
      const next = { ...prev };
      if (next[pair_id] === d) delete next[pair_id];
      else next[pair_id] = d;
      return next;
    });
  }

  function autoAcceptByThreshold() {
    const next: Record<string, Decision> = { ...decisions };
    for (const item of items) {
      if (item.status !== "pending") continue;
      if (item.jw_title >= threshold) next[item.pair_id] = "accept";
    }
    setDecisions(next);
  }

  // #3: GERÇEK "Tümünü seç" — görünür (filtrelenmiş) pending çiftlerin HEPSİNİ
  // 'accept' işaretler; hepsi zaten seçiliyse temizler (toggle). Sağ üstteki
  // "Tümünü göster" yalnız GÖRÜNÜMÜ değiştirir; seçim bu butonla yapılır.
  const pendingVisible = useMemo(() => visible.filter((i) => i.status === "pending"), [visible]);
  const allVisibleSelected = pendingVisible.length > 0 && pendingVisible.every((i) => decisions[i.pair_id] === "accept");

  function toggleSelectAllVisible() {
    setDecisions((prev) => {
      const next = { ...prev };
      for (const i of pendingVisible) {
        if (allVisibleSelected) delete next[i.pair_id];
        else next[i.pair_id] = "accept";
      }
      return next;
    });
  }

  async function applyDecisions() {
    if (counts.decided === 0) return;
    const acceptCount = Object.values(decisions).filter((v) => v === "accept").length;
    const rejectCount = Object.values(decisions).filter((v) => v === "reject").length;
    const skipCount = Object.values(decisions).filter((v) => v === "skip").length;
    const ok = await confirm({
      message: `${acceptCount} ${t("borderline.accepted")}, ${rejectCount} ${t("borderline.rejected")}, ${skipCount} ${t("borderline.skip")}.`,
      detail: t("borderline.bulkConfirm"),
    });
    if (!ok) return;

    setBusy(true);
    setError(null);
    try {
      const payload = Object.entries(decisions).map(([pair_id, decision]) => ({ pair_id, decision }));
      const r = await api.decideBorderline(projectId, payload);
      toast(
        t("borderline.appliedToast", { applied: r.applied, pending: r.pending_after }) +
        (r.snapshot ? `\n${t("disambiguate.snapshot")}: ${r.snapshot}` : ""),
        { tone: "success" },
      );
      await refresh();
      onApplied?.();
    } catch (e) {
      setError(translateApiError(t, e, "borderline.applyFailed"));
    } finally {
      setBusy(false);
    }
  }

  // Records (filtre) sayfasına gömülü — yüklenirken veya hiç belirsiz çift
  // yoksa sessizce gizlenir; kullanıcıyı meşgul etmez.
  if (loading) return null;
  if (items.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        {/* #2: başlık tıklanınca aç/kapa — varsayılan kapalı, sayaç rozetiyle */}
        <button
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-2 flex-1 text-left min-w-0"
          aria-expanded={open}
        >
          {open ? <ChevronDown className="h-4 w-4 text-white/70 flex-shrink-0" /> : <ChevronRight className="h-4 w-4 text-white/70 flex-shrink-0" />}
          <AlertTriangle className="h-4 w-4 text-warning flex-shrink-0" />
          <span className="font-semibold text-sm truncate">{t("borderline.title")}</span>
          {counts.pending > 0 && (
            <span className="flex-shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-warning-soft text-amber-700">
              {counts.pending}
            </span>
          )}
        </button>
        {open ? (
          <button
            onClick={refresh}
            className="text-xs text-white/70 hover:text-white flex items-center gap-1 flex-shrink-0"
            title={t("borderline.refresh")}
          >
            <RefreshCw className="h-3 w-3" /> {t("borderline.refresh")}
          </button>
        ) : (
          <span className="text-xs text-white/70 flex-shrink-0">{t("borderline.reviewToggle")}</span>
        )}
      </CardHeader>
      {open && (
      <CardBody className="space-y-3">
        {error && (
          <div className="rounded-lg border border-danger/30 bg-danger-soft px-3 py-2 text-xs text-red-700">
            {error}
          </div>
        )}

        {/* Sticky toolbar */}
        <div className="rounded-lg border border-border bg-bg-soft/40 p-3 space-y-2">
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <span className="font-medium text-ink">
              {counts.pending} {t("borderline.pending")}
            </span>
            <span className="text-muted">·</span>
            <span className="text-success">{counts.accept} {t("borderline.accepted")}</span>
            <span className="text-muted">·</span>
            <span className="text-danger">{counts.reject} {t("borderline.rejected")}</span>
            <span className="text-muted">·</span>
            <span className="text-muted">{counts.skip} {t("borderline.skip")}</span>
            <div className="ml-auto flex items-center gap-2">
              <button
                onClick={() => setStatusFilter("pending")}
                className={cn(
                  "text-[10px] px-2 py-0.5 rounded border",
                  statusFilter === "pending" ? "bg-brand-500 text-white border-brand-500" : "bg-white text-muted border-border",
                )}
              >
                {t("borderline.filterPending")}
              </button>
              <button
                onClick={() => setStatusFilter("all")}
                className={cn(
                  "text-[10px] px-2 py-0.5 rounded border",
                  statusFilter === "all" ? "bg-brand-500 text-white border-brand-500" : "bg-white text-muted border-border",
                )}
              >
                {t("borderline.filterAll")} ({items.length})
              </button>
            </div>
          </div>

          {/* Toplu kabul */}
          <div className="flex items-center gap-2 text-xs">
            <label className="flex items-center gap-1.5 text-muted">
              JW ≥
              <InfoTip text={t("help.jw")} />
              <input
                type="number"
                step={0.01}
                min={0.80}
                max={0.92}
                value={threshold}
                onChange={(e) => setThreshold(parseFloat(e.target.value) || 0.88)}
                className="w-20 rounded border border-border bg-white px-2 py-0.5 text-xs tabular-nums"
              />
            </label>
            <button
              onClick={autoAcceptByThreshold}
              className="text-xs px-2 py-0.5 rounded border border-success/40 bg-success-soft text-emerald-700 hover:bg-success-soft/80 flex items-center gap-1"
            >
              <CheckSquare className="h-3 w-3" /> {t("borderline.bulkMark")}
            </button>
            {/* #3: gerçek tümünü-seç (görünür pending hepsi) — toggle */}
            <button
              onClick={toggleSelectAllVisible}
              disabled={pendingVisible.length === 0}
              title={t("borderline.selectAllHint")}
              className="text-xs px-2 py-0.5 rounded border border-brand-300 bg-brand-50 text-brand-700 hover:bg-brand-100 flex items-center gap-1 disabled:opacity-50"
            >
              <ListChecks className="h-3 w-3" />
              {allVisibleSelected ? t("borderline.clearVisible") : t("borderline.selectAllVisible")}
            </button>
            <Button
              size="sm"
              onClick={applyDecisions}
              disabled={busy || counts.decided === 0}
              className="ml-auto bg-success hover:bg-success/90"
            >
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
              {t("borderline.applySelection")} ({counts.decided})
            </Button>
          </div>
        </div>

        {/* Boş durum */}
        {visible.length === 0 && (
          <div className="text-center py-12 text-muted text-sm">
            {counts.pending === 0 ? t("borderline.noBorderline") : t("borderline.noFilterMatch")}
          </div>
        )}

        {/* Pair listesi */}
        <div className="space-y-2">
          {visible.map((item) => (
            <BorderlineCard
              key={item.pair_id}
              pair={item}
              decision={decisions[item.pair_id]}
              onDecision={(d) => setDecision(item.pair_id, d)}
            />
          ))}
        </div>
      </CardBody>
      )}
    </Card>
  );
}


function BorderlineCard({ pair, decision, onDecision }: {
  pair: BorderlinePair;
  decision?: Decision;
  onDecision: (d: Decision) => void;
}) {
  const t = useT();
  const jwColor = pair.jw_title >= 0.88
    ? "bg-success-soft text-emerald-700 border-success/40"
    : pair.jw_title >= 0.82
      ? "bg-warning-soft text-amber-700 border-warning/40"
      : "bg-danger-soft text-red-700 border-danger/40";

  const decoStatusClass = decision === "accept"
    ? "border-success ring-2 ring-success/30"
    : decision === "reject"
      ? "border-danger ring-2 ring-danger/30"
      : decision === "skip"
        ? "border-muted ring-2 ring-muted/30"
        : "border-border";

  return (
    <div className={cn("rounded-lg border bg-white p-3 transition", decoStatusClass)}>
      {/* Header */}
      <div className="flex items-center gap-2 mb-2 text-xs flex-wrap">
        <span className="font-mono text-[10px] bg-bg-soft px-1.5 py-0.5 rounded text-muted">
          {pair.pair_id}
        </span>
        <span className={cn("text-[10px] font-bold px-1.5 py-0.5 rounded border", jwColor)}>
          JW {pair.jw_title.toFixed(3)}
        </span>
        <span className="text-[10px] text-muted">
          {t("borderline.confShort")} {pair.confidence.toFixed(2)}
        </span>
        <span className="text-[10px] text-muted truncate flex-1" title={pair.reason}>
          {pair.reason}
        </span>
        {pair.llm_suggestion && (
          <span className={cn(
            "text-[10px] font-bold px-1.5 py-0.5 rounded flex items-center gap-1",
            pair.llm_suggestion.verdict === "same" ? "bg-success-soft text-emerald-700"
            : pair.llm_suggestion.verdict === "different" ? "bg-danger-soft text-red-700"
            : "bg-bg-soft text-muted",
          )}>
            <Sparkles className="h-2.5 w-2.5" />
            LLM: {pair.llm_suggestion.verdict.toUpperCase()} ({(pair.llm_suggestion.confidence * 100).toFixed(0)}%)
          </span>
        )}
      </div>

      {/* Side-by-side comparison */}
      <div className="grid grid-cols-2 gap-3 mb-3">
        <RecordPanel label="WoS" record={pair.wos} compare={pair.scopus} />
        <RecordPanel label="Scopus" record={pair.scopus} compare={pair.wos} />
      </div>

      {/* Butonlar */}
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          onClick={() => onDecision("accept")}
          className={cn(
            "flex-1 justify-center",
            decision === "accept"
              ? "bg-success hover:bg-success/90 text-white"
              : "bg-white border border-success/40 text-emerald-700 hover:bg-success-soft",
          )}
        >
          <Check className="h-3.5 w-3.5" />
          {decision === "accept" ? t("borderline.acceptAccepted") : t("borderline.accept")}
        </Button>
        <Button
          size="sm"
          onClick={() => onDecision("reject")}
          className={cn(
            "flex-1 justify-center",
            decision === "reject"
              ? "bg-danger hover:bg-danger/90 text-white"
              : "bg-white border border-danger/40 text-red-700 hover:bg-danger-soft",
          )}
        >
          <X className="h-3.5 w-3.5" />
          {decision === "reject" ? t("borderline.rejectRejected") : t("borderline.reject")}
        </Button>
        <Button
          size="sm"
          onClick={() => onDecision("skip")}
          className={cn(
            "justify-center",
            decision === "skip"
              ? "bg-muted text-white"
              : "bg-white border border-border text-muted hover:bg-bg-soft",
          )}
        >
          <SkipForward className="h-3.5 w-3.5" />
          {t("borderline.skip")}
        </Button>
      </div>
    </div>
  );
}


function RecordPanel({ label, record, compare }: {
  label: string;
  record: BorderlinePair["wos"] | BorderlinePair["scopus"];
  compare: BorderlinePair["wos"] | BorderlinePair["scopus"];
}) {
  const t = useT();
  return (
    <div className="rounded border border-border bg-bg-soft/30 p-2 space-y-1 text-[11px]">
      <div className="text-[10px] font-bold uppercase tracking-wider text-muted">{label}</div>
      <Field label={t("borderline.fields.title")} v={record.title} other={compare.title} />
      <div className="grid grid-cols-2 gap-1">
        <Field label={t("borderline.fields.year")} v={record.year ?? ""} other={compare.year ?? ""} />
        <Field label={t("borderline.fields.surname")} v={record.surname ?? ""} other={compare.surname ?? ""} />
      </div>
      <Field label={t("borderline.fields.journal")} v={record.journal ?? ""} other={compare.journal ?? ""} />
      <div className="grid grid-cols-2 gap-1">
        <Field label={t("borderline.fields.volume")} v={record.volume ?? ""} other={compare.volume ?? ""} />
        {record.doi ? (
          <div>
            <div className="text-[9px] uppercase text-muted">{t("borderline.fields.doi")}</div>
            <a
              href={`https://doi.org/${record.doi}`}
              target="_blank" rel="noreferrer"
              className="text-brand-600 hover:underline truncate inline-flex items-center gap-0.5 max-w-full"
              onClick={(e) => e.stopPropagation()}
            >
              <span className="truncate font-mono text-[10px]">{record.doi}</span>
              <ExternalLink className="h-2.5 w-2.5 flex-shrink-0" />
            </a>
          </div>
        ) : (
          <Field label={t("borderline.fields.doi")} v="—" other="" />
        )}
      </div>
    </div>
  );
}


function Field({ label, v, other }: { label: string; v: string | number; other: string | number }) {
  const t = useT();
  const vs = String(v ?? "").trim();
  const os = String(other ?? "").trim();
  const diff = vs && os && vs.toLowerCase() !== os.toLowerCase();
  return (
    <div>
      <div className="text-[9px] uppercase text-muted">{label}</div>
      <div className={cn(
        "truncate",
        diff ? "text-warning font-medium" : "text-ink",
      )} title={vs}>
        {vs || <span className="text-muted/60">({t("common.none").toLowerCase()})</span>}
      </div>
    </div>
  );
}
