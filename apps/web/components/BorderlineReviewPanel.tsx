"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type BorderlinePair, translateApiError} from "@/lib/api-client";
import { Card, CardBody, CardHeader } from "./Card";
import { Button } from "./Button";
import {
  AlertTriangle, Check, X, SkipForward, CheckSquare, RefreshCw, Loader2,
  Sparkles, ExternalLink, ListChecks,
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
  // String-backed: serbest yazıma izin verir ("0.", boş vb. ara durumlar snap-back
  // yapmaz); sayıya parse edilip kullanılır, blur'da [0.80, 0.92] aralığına kelepçelenir.
  const [threshold, setThreshold] = useState("0.88");
  const [statusFilter, setStatusFilter] = useState<"pending" | "all">("pending");
  // Smart Merge adımında "kapı" (gate): önce uyarı + Evet/Hayır. "Evet" → inceleme
  // açılır; "Hayır" → gizlenir (belirsiz çiftler ayrı kalır, merge olduğu gibi tamamlanır).
  const [reviewing, setReviewing] = useState(false);
  const [dismissed, setDismissed] = useState(false);

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
    const thr = parseFloat(threshold);
    const cutoff = Number.isFinite(thr) ? thr : 0.88;
    const next: Record<string, Decision> = { ...decisions };
    for (const item of items) {
      if (item.status !== "pending") continue;
      if (item.jw_title >= cutoff) next[item.pair_id] = "accept";
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

  // Yüklenirken / hiç bekleyen belirsiz çift yokken / "Hayır" denmişken gizli kalır —
  // Smart Merge zaten tamamlandı; kapı yalnızca gerçekten belirsizlik varsa görünür.
  if (loading) return null;
  if (counts.pending === 0) return null;
  if (dismissed) return null;

  // ── KAPI (gate): önce sor — "manuel kontrol etmek ister misiniz?" ──
  if (!reviewing) {
    return (
      <Card className="ring-1 ring-warning/30">
        <CardBody className="p-4 sm:p-5">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-warning-soft text-warning">
              <AlertTriangle className="h-5 w-5" />
            </div>
            <div className="min-w-0 flex-1">
              <h3 className="font-semibold text-sm text-ink">{t("borderline.gateTitle")}</h3>
              <p className="mt-1 text-sm text-muted leading-relaxed">
                {t("borderline.gatePrompt", { pending: counts.pending })}
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  onClick={() => setReviewing(true)}
                  className="bg-warning hover:bg-warning/90 text-white"
                >
                  <ListChecks className="h-3.5 w-3.5" /> {t("borderline.gateYes")}
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => { setDismissed(true); toast(t("borderline.gateDismissedToast"), { tone: "success" }); }}
                >
                  <Check className="h-3.5 w-3.5" /> {t("borderline.gateNo")}
                </Button>
              </div>
            </div>
          </div>
        </CardBody>
      </Card>
    );
  }

  // ── İNCELEME: "Evet" → çiftleri sor; "Kaydet ve tamamla" ile uygula ──
  return (
    <Card className="ring-1 ring-warning/30">
      <CardHeader>
        <AlertTriangle className="h-4 w-4 text-warning flex-shrink-0" />
        <span className="font-semibold text-sm flex-1 truncate">{t("borderline.title")}</span>
        <button
          onClick={refresh}
          className="text-xs text-white/70 hover:text-white flex items-center gap-1 flex-shrink-0"
          title={t("borderline.refresh")}
        >
          <RefreshCw className="h-3 w-3" /> {t("borderline.refresh")}
        </button>
        <button
          onClick={() => setReviewing(false)}
          className="text-xs text-white/70 hover:text-white flex-shrink-0"
        >
          {t("borderline.gateBack")}
        </button>
      </CardHeader>
      <CardBody className="space-y-3">
        <p className="text-xs text-muted leading-relaxed">{t("borderline.description")}</p>
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
                onChange={(e) => setThreshold(e.target.value)}
                onBlur={(e) => {
                  const v = parseFloat(e.target.value);
                  setThreshold(Number.isFinite(v) ? String(Math.min(0.92, Math.max(0.80, v))) : "0.88");
                }}
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
              {t("borderline.saveAndComplete")} ({counts.decided})
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
