"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type QualityStats, type JobInfo, type FillReport } from "@/lib/api-client";
import { Card, CardBody, CardHeader } from "./Card";
import {
  Heart, BarChart3, Sparkles, AlertCircle, CheckCircle2, Loader2, Play, X, FileText,
  Table2, Download,
} from "lucide-react";
import { toPng } from "html-to-image";
import { useT } from "@/lib/i18n";
import { useConfirm } from "./Dialogs";
import { cn } from "@/lib/cn";

type Props = {
  projectId: string;
  onChanged?: () => void;          // doldurma bitince dışarıyı (tabloyu) tazele
};

type CombinedJob = {
  jobId: string;
  progress: number;
  status: string;
  log_tail: string[];
  result?: any;
};

/**
 * Veri Kalitesi & Doldurma paneli (Harmonization adımı).
 *
 * Tek "Eksik alanları doldur" akışı: DOI başına TEK API çağrısı (tüm boş alanlar)
 * + ML geçişi (DE/ID/SC/WC) → tek job, tek ilerleme çubuğu. Alan-bazlı tek-tek
 * doldurma kaldırıldı (gereksiz tekrar API çağrısıydı).
 */
export function QualityDashboard({ projectId, onChanged }: Props) {
  const t = useT();
  const confirm = useConfirm();
  const [stats, setStats] = useState<QualityStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(true);
  const [job, setJob] = useState<CombinedJob | null>(null);
  // Geçmiş 'Fill all' raporu (audit'ten) — sayfa yenilense bile kalır.
  const [savedReport, setSavedReport] = useState<FillReport | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const refreshStats = useCallback(async () => {
    try {
      setStats(await api.qualityStats(projectId));
    } catch { /* sessiz */ }
  }, [projectId]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api.qualityStats(projectId)
      .then((s) => { if (alive) { setStats(s); setLoading(false); } })
      .catch(() => { if (alive) setLoading(false); });
    // Geçmiş raporu çek — varsa banner yerine rapor gösterilir.
    api.lastFillReport(projectId)
      .then((r) => { if (alive) setSavedReport(r.report); })
      .catch(() => {});
    return () => {
      alive = false;
      if (esRef.current) { esRef.current.close(); esRef.current = null; }
    };
  }, [projectId]);

  const running = !!job && (job.status === "running" || job.status === "queued");

  async function startFillAll() {
    if (running || !stats) return;
    const ok = await confirm({
      title: t("records.quality.startAllTitle"),
      message: t("records.quality.startAllConfirm", { n: fillableCount }),
      detail: t("records.quality.startAllFreeNote"),
      confirmLabel: t("records.quality.startAll"),
    });
    if (!ok) return;
    try {
      const { job_id } = await api.startFillAll(projectId);
      setJob({ jobId: job_id, progress: 0, status: "queued", log_tail: [] });
      const es = new EventSource(api.jobStreamUrl(job_id));
      esRef.current = es;
      es.addEventListener("update", (ev) => {
        const e = ev as MessageEvent;
        if (!e.data) return;
        try {
          const info: JobInfo = JSON.parse(e.data);
          setJob({ jobId: job_id, progress: info.progress, status: info.status, log_tail: info.log_tail, result: info.result });
          if (info.status === "completed" || info.status === "failed" || info.status === "cancelled") {
            es.close(); esRef.current = null;
            refreshStats();
            onChanged?.();
            if (info.status === "completed") {
              // Kalıcı raporu audit'ten tazele; canlı job kartını kapat (rapor savedReport'tan gösterilir).
              api.lastFillReport(projectId).then((r) => setSavedReport(r.report)).catch(() => {});
              setJob(null);
            } else {
              setTimeout(() => setJob(null), 4000);
            }
          }
        } catch { /* yut */ }
      });
      es.addEventListener("done", () => { es.close(); esRef.current = null; });
      es.onerror = () => {
        es.close(); esRef.current = null;
        // Bağlantı koptuysa (terminal 'update' gelmeden) işi 'failed' yap ki UI takılı kalmasın.
        setJob((j) => (j && (j.status === "running" || j.status === "queued")) ? { ...j, status: "failed" } : j);
        refreshStats();
      };
    } catch { /* sessiz */ }
  }

  async function cancelFillAll() {
    if (!job) return;
    try { await api.cancelJob(job.jobId); } catch { /* yut */ }
  }

  if (loading) {
    return (
      <Card>
        <CardBody className="text-center py-8 text-muted text-sm">{t("records.quality.loadingQuality")}</CardBody>
      </Card>
    );
  }
  if (!stats) return null;

  const healthPct = Math.round(stats.health_score * 100);
  const healthColor = healthPct >= 90 ? "text-emerald-300" : healthPct >= 70 ? "text-amber-300" : "text-red-300";

  const filledHigh = stats.fields.filter((f) => f.available && f.fill_rate >= 0.9);
  const filledMid = stats.fields.filter((f) => f.available && f.fill_rate >= 0.5 && f.fill_rate < 0.9);
  const filledLow = stats.fields.filter((f) => f.available && f.fill_rate < 0.5);
  const fillableCount = stats.fields.filter((f) => f.available && f.missing > 0).length;
  const jobPct = Math.round((job?.progress ?? 0) * 100);
  const lastLog = job && job.log_tail.length > 0 ? job.log_tail[job.log_tail.length - 1] : "";

  return (
    <Card>
      <CardHeader>
        <Heart className={cn("h-4 w-4", healthColor)} />
        <h2 className="font-semibold text-sm flex-1">{t("records.quality.title")}</h2>
        <div className="flex items-center gap-2">
          <span className={cn("text-xs font-semibold tabular-nums", healthColor)} title={t("records.quality.healthScoreHint")}>
            {t("records.quality.healthScore")} {healthPct}%
          </span>
          <button onClick={() => setExpanded(!expanded)} className="text-xs text-white/70 hover:text-white">
            {expanded ? t("common.close").toLowerCase() : t("common.open").toLowerCase()}
          </button>
        </div>
      </CardHeader>
      {expanded && (
        <CardBody className="space-y-4">
          {/* Veri sağlığı taraması — belirgin doldurma CTA. Çalışırken (running) veya
              henüz hiç rapor yokken (savedReport yok) gösterilir. Geçmiş rapor varsa
              banner yerine aşağıdaki kalıcı rapor görünür. */}
          {(running || !savedReport) && (
          <div className={cn(
            "rounded-xl p-4 text-white shadow-lg ring-1 transition",
            job?.status === "failed" ? "bg-gradient-to-r from-rose-500 to-red-600 ring-red-300/40"
              : "bg-gradient-to-r from-cyan-500 to-brand-600 ring-cyan-300/40",
          )}>
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-xl bg-white/15 flex items-center justify-center flex-shrink-0">
                {running ? <Loader2 className="h-6 w-6 animate-spin" />
                  : job?.status === "completed" ? <CheckCircle2 className="h-6 w-6" />
                  : job?.status === "failed" ? <AlertCircle className="h-6 w-6" />
                  : <Sparkles className="h-6 w-6" />}
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="text-base font-extrabold leading-tight">{t("records.quality.scanTitle")}</h3>
                <p className="text-xs text-white/85 mt-0.5 truncate">
                  {running ? (lastLog || t("records.quality.starting"))
                    : (job?.status === "completed" || job?.status === "failed") ? (lastLog || t("records.quality.scanTitle"))
                    : fillableCount > 0 ? t("records.quality.scanDesc", { n: fillableCount })
                    : t("records.quality.scanAllGood")}
                </p>
              </div>
              {running ? (
                <div className="flex items-center gap-3 flex-shrink-0">
                  <span className="text-2xl font-extrabold tabular-nums">%{jobPct}</span>
                  <button
                    onClick={cancelFillAll}
                    className="flex items-center gap-1.5 rounded-lg bg-white/15 hover:bg-white/25 px-3 py-2 text-sm font-semibold transition"
                  >
                    <X className="h-4 w-4" /> {t("records.quality.stopAll")}
                  </button>
                </div>
              ) : (
                <button
                  onClick={startFillAll}
                  disabled={fillableCount === 0}
                  title={t("records.quality.startAllHint")}
                  className="group flex items-center gap-2.5 rounded-xl bg-white px-5 py-3 text-cyan-700 font-extrabold shadow-md ring-1 ring-white/60 transition hover:scale-[1.03] hover:shadow-xl disabled:opacity-60 disabled:cursor-not-allowed disabled:hover:scale-100 flex-shrink-0"
                >
                  <Play className="h-5 w-5 fill-current" />
                  <span>{t("records.quality.startAll")}</span>
                  {fillableCount > 0 && <span className="ml-1 rounded-lg bg-cyan-100 px-2 py-0.5 text-sm tabular-nums">{fillableCount}</span>}
                </button>
              )}
            </div>
            {running && (
              <div className="h-1.5 mt-3 rounded-full bg-white/20 overflow-hidden">
                <div className="h-full bg-white transition-all" style={{ width: `${jobPct}%` }} />
              </div>
            )}
          </div>
          )}

          {/* Kalıcı enrichment raporu — geçmişte 'Fill all' yapıldıysa (savedReport),
              çalışmıyorken banner yerine bu gösterilir. "Run again" yeniden çalıştırır. */}
          {savedReport && !running && (
            <div className="rounded-xl border border-success/40 bg-success-soft/30 p-4">
              <div className="flex items-start gap-3">
                <div className="w-10 h-10 rounded-lg bg-success/15 text-success flex items-center justify-center flex-shrink-0">
                  <CheckCircle2 className="h-5 w-5" />
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="font-bold text-sm text-ink">{t("records.quality.reportTitle")}</h3>
                  <p className="text-xs text-muted mt-0.5">
                    {savedReport.api?.total != null
                      ? t("records.quality.reportSummary", { enriched: savedReport.enriched ?? 0, scanned: savedReport.api.total })
                      : t("records.quality.reportSummaryShort", { enriched: savedReport.enriched ?? 0 })}
                  </p>
                  {(savedReport.doi?.filled ?? 0) > 0 && (
                    <p className="text-xs font-medium text-emerald-700 mt-0.5">
                      {t("records.quality.reportDois", { n: savedReport.doi!.filled! })}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <button onClick={startFillAll} disabled={fillableCount === 0}
                    className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-cyan-500 text-white hover:bg-cyan-600 disabled:opacity-50 flex items-center gap-1.5">
                    <Play className="h-3 w-3 fill-current" /> {t("records.quality.runAgain")}
                    {fillableCount > 0 && <span className="rounded bg-white/20 px-1 tabular-nums">{fillableCount}</span>}
                  </button>
                </div>
              </div>
              {savedReport.per_field_fill && (
                <div className="mt-3 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-1.5">
                  {Object.entries(savedReport.per_field_fill).map(([f, v]) => {
                    const b = Math.round((v.before ?? 0) * 100);
                    const a = Math.round((v.after ?? 0) * 100);
                    const gained = a > b;
                    return (
                      <div key={f} className={cn("rounded-md border bg-white px-2 py-1 text-[11px] flex items-center gap-1.5", gained ? "border-success/60" : "border-border")}>
                        <span className="font-mono text-[10px] text-muted">{f}</span>
                        <span className="ml-auto tabular-nums text-ink">
                          {b}% → <span className={gained ? "font-bold text-emerald-700" : "text-muted"}>{a}%</span>
                          {gained && <span className="ml-1 font-bold text-emerald-700">+{a - b}</span>}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
              <p className="text-[10px] text-muted mt-2">
                {t("records.quality.reportOverall", { before: Math.round((savedReport.fill_rate_before ?? 0) * 100), after: Math.round((savedReport.fill_rate_after ?? 0) * 100) })}
                {savedReport.snapshot ? ` · ${t("disambiguate.snapshot")}: ${String(savedReport.snapshot).split(/[\\/]/).pop()}` : ""}
              </p>
            </div>
          )}

          {/* Özet kartlar */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard icon={<FileText className="h-4 w-4" />} label={t("records.quality.totalRecords")}
              value={stats.total_records.toLocaleString()} accent="brand" />
            <StatCard icon={<CheckCircle2 className="h-4 w-4" />} label={t("records.quality.highFill")}
              value={`${filledHigh.length}`} hint="≥90%" accent="success" />
            <StatCard icon={<AlertCircle className="h-4 w-4" />} label={t("records.quality.midFill")}
              value={`${filledMid.length}`} hint="50–90%" accent="warning" />
            <StatCard icon={<AlertCircle className="h-4 w-4" />} label={t("records.quality.lowFill")}
              value={`${filledLow.length}`} hint="<50%" accent="danger" />
          </div>

          {/* Alan-bazlı doluluk — tek bar: her alan eşit dilim; dolu=yeşil (solda), eksik=alan rengi (sağda) */}
          <div>
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted flex items-center gap-1 mb-2">
              <BarChart3 className="h-3 w-3" /> {t("records.quality.fieldFillRates")}
            </h3>
            {(() => {
              const fields = stats.fields.filter((f) => f.available);
              const n = fields.length || 1;
              const w = 100 / n;                                            // her alanın bardaki eşit payı
              const greenPct = fields.reduce((s, f) => s + w * f.fill_rate, 0); // tüm dolu paylar tek havuzda
              const missing = fields.filter((f) => f.fill_rate < 1);
              return (
                <div className="flex h-7 rounded-md overflow-hidden border border-border mb-1.5">
                  {/* Sol: tüm dolu alanların TEK havuzu — yeşil blok, üzerinde toplam doluluk */}
                  <div
                    className="h-full bg-emerald-500 flex items-center justify-center"
                    style={{ width: `${greenPct}%` }}
                    title={`${Math.round(greenPct)}% ${t("records.quality.filledShort")}`}
                  >
                    <span className="font-mono text-[10px] font-bold text-white whitespace-nowrap drop-shadow-[0_1px_1px_rgba(0,0,0,0.5)]">
                      {Math.round(greenPct)}%
                    </span>
                  </div>
                  {/* Sağ: her alanın eksik payı — alan rengine göre yığılmış */}
                  {missing.map((f) => (
                    <div
                      key={f.field}
                      className="h-full border-l border-white/70"
                      style={{ width: `${w * (1 - f.fill_rate)}%`, backgroundColor: fieldColor(f.field) }}
                      title={`${f.field} · ${fieldLabel(t, f.field, f.label)} — ${Math.round((1 - f.fill_rate) * 100)}% ${t("records.quality.missingShort")}`}
                    />
                  ))}
                </div>
              );
            })()}
            <p className="text-[10px] text-muted mb-2">{t("records.quality.barLegend")}</p>
            <div className="flex flex-wrap gap-1.5">
              {stats.fields.filter((f) => f.available).map((f) => (
                <FieldChip key={f.field} field={f} />
              ))}
            </div>
            <p className="text-[10px] text-muted mt-2 leading-relaxed">
              <Sparkles className="h-3 w-3 inline mr-0.5 text-brand-500" />
              {t("records.quality.fillHint")}
            </p>
          </div>

          {/* #6: Biblioshiny-tarzı Genel Bakış tablosu + CSV/XLSX/PNG indirme */}
          <QualityOverview projectId={projectId} stats={stats} />
        </CardBody>
      )}
    </Card>
  );
}

/** Genel Bakış — alan-bazlı özet tablo + indirme (CSV/XLSX backend, PNG istemci-tarafı). */
function QualityOverview({ projectId, stats }: { projectId: string; stats: QualityStats }) {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);
  const fields = stats.fields.filter((f) => f.available);

  function downloadFile(fmt: "csv" | "xlsx") {
    const a = document.createElement("a");
    a.href = api.qualityOverviewUrl(projectId, fmt);
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  async function downloadPng() {
    if (!ref.current) return;
    try {
      const dataUrl = await toPng(ref.current, { backgroundColor: "#ffffff", pixelRatio: 2 });
      const a = document.createElement("a");
      a.href = dataUrl;
      a.download = "data_health.png";
      a.click();
    } catch { /* görsel üretilemezse sessiz */ }
  }

  const o = (k: string) => t(`records.quality.overview.${k}`);

  return (
    <div>
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted flex items-center gap-1 flex-1">
          <Table2 className="h-3 w-3" /> {o("title")}
        </h3>
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-muted flex items-center gap-1"><Download className="h-3 w-3" /> {o("download")}:</span>
          <button onClick={() => downloadFile("csv")} className="text-[11px] font-medium px-2 py-0.5 rounded border border-border bg-white hover:border-brand-300 text-ink">CSV</button>
          <button onClick={() => downloadFile("xlsx")} className="text-[11px] font-medium px-2 py-0.5 rounded border border-border bg-white hover:border-brand-300 text-ink">XLSX</button>
          <button onClick={downloadPng} className="text-[11px] font-medium px-2 py-0.5 rounded border border-border bg-white hover:border-brand-300 text-ink">PNG</button>
        </div>
      </div>
      <div ref={ref} className="overflow-x-auto rounded-lg border border-border bg-white">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-bg-soft text-muted text-[10px] uppercase tracking-wide">
              <th className="text-left px-2 py-1.5 font-semibold">{o("colField")}</th>
              <th className="text-left px-2 py-1.5 font-semibold">{o("colLabel")}</th>
              <th className="text-right px-2 py-1.5 font-semibold">{o("colTotal")}</th>
              <th className="text-right px-2 py-1.5 font-semibold">{o("colFilled")}</th>
              <th className="text-right px-2 py-1.5 font-semibold">{o("colMissing")}</th>
              <th className="text-right px-2 py-1.5 font-semibold">{o("colFillRate")}</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((f) => {
              const pct = Math.round(f.fill_rate * 100);
              const pctColor = pct >= 90 ? "text-emerald-700" : pct >= 50 ? "text-amber-700" : "text-red-600";
              return (
                <tr key={f.field} className="border-t border-border">
                  <td className="px-2 py-1 font-mono text-[10px] text-muted">{f.field}</td>
                  <td className="px-2 py-1 text-ink">{fieldLabel(t, f.field, f.label)}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{f.total.toLocaleString()}</td>
                  <td className="px-2 py-1 text-right tabular-nums text-emerald-700">{f.filled.toLocaleString()}</td>
                  <td className="px-2 py-1 text-right tabular-nums text-muted">{f.missing.toLocaleString()}</td>
                  <td className={cn("px-2 py-1 text-right tabular-nums font-semibold", pctColor)}>{pct}%</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-muted mt-1.5">{o("hint")}</p>
    </div>
  );
}

function StatCard({ icon, label, value, hint, accent = "brand" }: {
  icon: React.ReactNode; label: string; value: string; hint?: string;
  accent?: "brand" | "success" | "warning" | "danger";
}) {
  const accentMap = {
    brand: "bg-brand-50 text-brand-700 border-brand-200",
    success: "bg-success-soft text-emerald-700 border-success/30",
    warning: "bg-warning-soft text-amber-700 border-warning/30",
    danger: "bg-danger-soft text-red-700 border-danger/30",
  };
  return (
    <div className={cn("rounded-lg border px-3 py-2.5", accentMap[accent])}>
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide opacity-80">
        {icon}{label}
      </div>
      <div className="text-xl font-bold mt-1 tabular-nums">{value}</div>
      {hint && <div className="text-[10px] opacity-70 mt-0.5">{hint}</div>}
    </div>
  );
}

/** Alan → ayırt edici renk: bar'daki "eksik" diliminin ve aşağıdaki kutunun sol kenar rengi. */
// Kurumsal paletten (lacivert/mor/petrol/amber/kırmızı aileleri) ayırt
// edilebilir MAT kategorik tonlar — UI ile tutarlı, gökkuşağı değil.
const FIELD_COLORS: Record<string, string> = {
  DI: "#38597f", AB: "#74398c", TI: "#0f766e", AU: "#8e5ba5", PY: "#264667",
  DE: "#a16207", ID: "#c8982f", WC: "#b91c1c", SC: "#d05c5c", C1: "#7593b5",
  EM: "#9a1818", CR: "#5f6f85", TC: "#602a76", LA: "#c43a3a",
  SO: "#28837b", AF: "#4f1964",
};
function fieldColor(f: string): string {
  return FIELD_COLORS[f] ?? "#94A3B8";
}

/** Alan kodu -> yerelleştirilmiş etiket (recordDetail.fields.*); anahtar yoksa backend label'a düşer. */
function fieldLabel(t: (k: string) => string, code: string, fallback: string): string {
  const k = `recordDetail.fields.${code}`;
  const v = t(k);
  return !v || v === k ? fallback : v;
}

/** Kompakt alan kutusu — sol kenar rengi bar'daki alan rengiyle eşleşir; % doluluk sağlığını gösterir. */
function FieldChip({ field }: {
  field: { field: string; label: string; hint: string; total: number; filled: number; missing: number; fill_rate: number };
}) {
  const t = useT();
  const pct = Math.round(field.fill_rate * 100);
  const pctColor = pct >= 90 ? "text-emerald-700" : pct >= 50 ? "text-amber-700" : "text-red-600";
  const label = fieldLabel(t, field.field, field.label);
  return (
    <div
      title={`${label} · ${field.filled.toLocaleString()} ${t("records.quality.filledShort")} · ${field.missing.toLocaleString()} ${t("records.quality.missingShort")}`}
      className="flex items-center gap-1.5 pl-2 pr-2.5 py-1 rounded-md bg-white border border-border border-l-[3px]"
      style={{ borderLeftColor: fieldColor(field.field) }}
    >
      <span className="font-mono text-[10px] text-muted">{field.field}</span>
      <span className="text-[11px] text-ink truncate max-w-[8rem]">{label}</span>
      <span className={cn("ml-1 text-[11px] font-semibold tabular-nums", pctColor)}>{pct}%</span>
    </div>
  );
}
