"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api, translateApiError, type FilterResponse, type FilterSpec, type Preset } from "@/lib/api-client";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { Button } from "@/components/Button";
import { FilterPanel } from "@/components/FilterPanel";
import { RecordsTable } from "@/components/RecordsTable";
import { RecordDetailDrawer } from "@/components/RecordDetailDrawer";
import { BulkActionBar } from "@/components/BulkActionBar";
import { AuditLogPanel } from "@/components/AuditLogPanel";
import { BorderlineReviewPanel } from "@/components/BorderlineReviewPanel";
import { PageHeader } from "@/components/PageHeader";
import { ArrowRight, Save, X, Star, ChevronRight, Sparkles, FileOutput, SkipForward } from "lucide-react";
import { useT } from "@/lib/i18n";
import { useConfirm } from "@/components/Dialogs";
import { useProjectId } from "@/lib/use-project-id";
import { StepNav } from "@/components/StepNav";

const EMPTY: FilterSpec = {};
const PAGE_SIZE = 50;

export default function RecordsPage() {
  const id = useProjectId();
  const t = useT();
  const confirm = useConfirm();
  const [spec, setSpec] = useState<FilterSpec>(EMPTY);
  const [data, setData] = useState<FilterResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetName, setPresetName] = useState("");
  const [activePreset, setActivePreset] = useState<string | null>(null);
  const [selectedRecord, setSelectedRecord] = useState<Record<string, string | null> | null>(null);
  const [editStart, setEditStart] = useState(false);
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [showAudit, setShowAudit] = useState(false);
  const debounceRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const retryRef = useRef<number | null>(null);

  const refresh = useCallback(async (off = 0, retryCount = 0) => {
    // Önceki isteği iptal et (race condition koruması)
    if (abortRef.current) abortRef.current.abort();
    if (retryRef.current) { window.clearTimeout(retryRef.current); retryRef.current = null; }
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    try {
      const res = await api.filterRecords(id, {
        spec, offset: off, limit: PAGE_SIZE, include_facets: true,
      }, ctrl.signal);
      if (ctrl.signal.aborted) return;
      setData(res); setError(null);
    } catch (e) {
      // Aborted istek için state güncelleme yapma
      if (ctrl.signal.aborted || (e instanceof DOMException && e.name === "AbortError")) return;
      // "Failed to fetch" gibi ağ hataları → otomatik 1 deneme daha (backend reload sırasında olabilir)
      const msg = e instanceof Error ? e.message : String(e);
      const isTransient = msg.includes("Failed to fetch") || msg.includes("NetworkError") || msg.includes("ERR_");
      if (isTransient && retryCount < 1) {
        retryRef.current = window.setTimeout(() => { refresh(off, retryCount + 1); }, 800);
        return;
      }
      setError(translateApiError(t, e, "records.queryFailed")); setData(null);
    }
    finally {
      if (!ctrl.signal.aborted) setLoading(false);
    }
  }, [id, spec, t]);

  useEffect(() => {
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => { refresh(0); }, 250);
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
      if (retryRef.current) window.clearTimeout(retryRef.current);
      if (abortRef.current) abortRef.current.abort();
    };
  }, [refresh]);

  useEffect(() => {
    api.listPresets(id).then(setPresets).catch(() => {});
  }, [id]);

  const activeCount = useMemo(() => {
    return Object.entries(spec).filter(([, v]) => {
      if (v == null) return false;
      if (Array.isArray(v)) return v.length > 0;
      if (typeof v === "object") {
        if ("query" in v) return !!v.query;
        if ("min" in v || "max" in v) return v.min != null || v.max != null;
        if ("missing" in v || "has" in v) {
          const q = v as { missing?: string[]; has?: string[] };
          return (q.missing?.length ?? 0) + (q.has?.length ?? 0) > 0;
        }
      }
      return false;
    }).length;
  }, [spec]);

  async function savePreset() {
    if (!presetName.trim()) return;
    await api.savePreset(id, presetName.trim(), spec);
    await api.addAuditEntry(id, {
      kind: "filter_save",
      title: t("records.presets.savedAuditTitle", { name: presetName.trim() }),
      details: { filter_count: activeCount, spec },
      user_action: "save_preset",
    }).catch(() => {});
    setPresetName("");
    setPresets(await api.listPresets(id));
  }

  async function deletePreset(name: string) {
    if (!(await confirm({ message: t("records.presets.deleteConfirm", { name }), tone: "danger" }))) return;
    await api.deletePreset(id, name);
    if (activePreset === name) setActivePreset(null);
    setPresets(await api.listPresets(id));
  }

  function loadPreset(p: Preset) {
    setSpec(p.spec);
    setActivePreset(p.name);
  }

  async function handleDeleteRow(row: Record<string, string | null>) {
    const uid = row.UID || undefined;
    const doi = row.DI || undefined;
    if (!uid && !doi) return;
    if (!(await confirm({ message: t("records.deleteRowConfirm"), tone: "danger" }))) return;
    try {
      await api.deleteRecords(id, uid ? { uids: [uid] } : { dois: [doi!] });
      setSelectedRows((prev) => { const n = new Set(prev); if (uid) n.delete(uid); return n; });
      refresh(0);
    } catch (e) {
      setError(translateApiError(t, e, "records.queryFailed"));
    }
  }

  return (
    <>
      <PageHeader
        title={t("records.title")}
        subtitle={t("records.subtitle")}
        badges={[{ label: t("records.stepBadge"), tone: "neutral" }]}
        right={
          <StepNav
            onHistory={() => setShowAudit(true)}
            nextHref={`/projects/${id}/enrich`}
            nextLabel={t("nav.ai")}
          />
        }
      />
      <div className="max-w-[1480px] mx-auto px-6 py-6 space-y-4">

        {error && (
          <div className="rounded-lg border border-danger/30 bg-danger-soft px-4 py-2 text-sm text-red-700">
            {error}{error.includes("Merge") || error.includes("Birleştir") || error.includes("merge") ? (
              <Link href={`/projects/${id}/merge`} className="ml-2 underline">{t("records.goToMerge")}</Link>
            ) : null}
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-[300px_1fr] gap-4">
          {/* Sol panel: presets + filter */}
          <aside className="space-y-3">
            <Card>
              <CardHeader className="py-2.5">
                <span className="font-semibold text-sm flex items-center gap-1.5">
                  <Star className="h-3.5 w-3.5 text-warning" /> {t("records.presets.title")}
                </span>
                <span className="text-[10px] text-muted ml-auto">{presets.length}</span>
              </CardHeader>
              <CardBody className="space-y-2 py-2.5">
                <div className="flex gap-1">
                  <input
                    value={presetName}
                    onChange={(e) => setPresetName(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") savePreset(); }}
                    placeholder={t("records.presets.namePlaceholder")}
                    className="flex-1 rounded-md border border-border bg-white px-2 py-1.5 text-xs focus:outline-none focus:border-brand-500"
                  />
                  <Button size="sm" variant="secondary" onClick={savePreset} disabled={!presetName.trim() || activeCount === 0} title={t("records.presets.save")}>
                    <Save className="h-3.5 w-3.5" />
                  </Button>
                </div>
                {activeCount === 0 && (
                  <p className="text-[10px] text-muted">{t("records.presets.hint")}</p>
                )}
                {presets.length === 0 ? (
                  <p className="text-xs text-muted py-1">{t("records.presets.noPresets")}.</p>
                ) : (
                  <ul className="space-y-0.5">
                    {presets.map((p) => (
                      <li key={p.name} className="group">
                        <div className={`flex items-center gap-1 rounded text-xs ${activePreset === p.name ? "bg-brand-50" : ""}`}>
                          <button
                            onClick={() => loadPreset(p)}
                            className={`flex-1 text-left truncate px-2 py-1.5 rounded flex items-center gap-1.5 hover:bg-bg-soft ${activePreset === p.name ? "text-brand-700 font-medium" : "text-ink"}`}
                          >
                            <ChevronRight className={`h-3 w-3 flex-shrink-0 ${activePreset === p.name ? "text-brand-500" : "text-muted"}`} />
                            {p.name}
                          </button>
                          <button
                            onClick={() => deletePreset(p.name)}
                            className="opacity-0 group-hover:opacity-100 p-1 text-muted hover:text-danger transition"
                            title={t("records.presets.delete")}
                          >
                            <X className="h-3 w-3" />
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardBody className="py-3">
                <FilterPanel
                  spec={spec}
                  facetsAll={data?.facets_all}
                  onChange={(next) => { setSpec(next); setActivePreset(null); }}
                  onReset={() => { setSpec(EMPTY); setActivePreset(null); }}
                />
              </CardBody>
            </Card>
          </aside>

          {/* Sağ panel: özet + tablo */}
          <section className="space-y-3 min-w-0">
            <StatsBar
              data={data}
              activeCount={activeCount}
              loading={loading}
              spec={spec}
              onClear={() => setSpec(EMPTY)}
            />

            {/* Belirsiz çiftler — Smart Merge'de otomatik ayrı tutuldu; isteyen
                burada elle birleştirir (hiç belirsizlik yoksa görünmez). */}
            <BorderlineReviewPanel projectId={id} onApplied={() => refresh(0)} />

            {data ? (
              <RecordsTable
                data={data}
                onPage={refresh}
                onRowClick={(r) => { setSelectedRecord(r); setEditStart(false); }}
                onEditRow={(r) => { setSelectedRecord(r); setEditStart(true); }}
                onDeleteRow={handleDeleteRow}
                selected={selectedRows}
                onSelectionChange={setSelectedRows}
              />
            ) : !error ? (
              <Card>
                <CardBody className="text-center py-12 text-muted text-sm">
                  {t("common.loading")}
                </CardBody>
              </Card>
            ) : null}

            {/* Sonraki adım kararı — Records → AI (opsiyonel) veya Export */}
            {data && data.total > 0 && <NextStepCard projectId={id} />}
          </section>
        </div>
      </div>

      <RecordDetailDrawer
        record={selectedRecord}
        projectId={id}
        startEditing={editStart}
        onSaved={() => refresh(0)}
        onClose={() => { setSelectedRecord(null); setEditStart(false); }}
      />

      <BulkActionBar
        projectId={id}
        selected={selectedRows}
        onClear={() => setSelectedRows(new Set())}
        onChanged={() => {
          refresh(0);
        }}
      />

      <AuditLogPanel
        projectId={id}
        open={showAudit}
        onClose={() => setShowAudit(false)}
      />
    </>
  );
}

function StatsBar({ data, activeCount, loading, spec, onClear }: {
  data: FilterResponse | null;
  activeCount: number;
  loading: boolean;
  spec: FilterSpec;
  onClear: () => void;
}) {
  const t = useT();
  if (!data) return null;
  const totalAll = data.facets_all?.total ?? data.total;
  const pct = totalAll > 0 ? Math.round((data.total / totalAll) * 100) : 100;

  return (
    <div className="rounded-xl border border-border bg-bg-card shadow-card px-4 py-3 flex items-center gap-4 flex-wrap">
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-bold text-ink tabular-nums">{data.total.toLocaleString()}</span>
        <span className="text-xs text-muted">{t("records.matchingRecords")}</span>
      </div>
      <div className="h-8 w-px bg-border" />
      <div className="flex items-baseline gap-2">
        <span className="text-sm font-semibold text-muted tabular-nums">{totalAll.toLocaleString()}</span>
        <span className="text-xs text-muted">{t("common.total").toLowerCase()} · {pct}%</span>
      </div>

      {/* Progress bar */}
      <div className="flex-1 min-w-[120px] max-w-xs">
        <div className="h-1.5 rounded-full bg-bg-soft overflow-hidden">
          <div className="h-full bg-brand-500 transition-all" style={{ width: `${pct}%` }} />
        </div>
      </div>

      {activeCount > 0 && (
        <>
          <ActiveFilterChips spec={spec} />
          <button onClick={onClear} className="text-xs text-muted hover:text-danger flex items-center gap-1">
            <X className="h-3 w-3" /> {t("records.filter.clearAll")}
          </button>
        </>
      )}

      {loading && (
        <span className="text-[11px] text-brand-600 animate-pulse ml-auto">{t("common.loading")}</span>
      )}
    </div>
  );
}

function ActiveFilterChips({ spec }: { spec: FilterSpec }) {
  const t = useT();
  const chips: { label: string }[] = [];
  if (spec.year && (spec.year.min != null || spec.year.max != null))
    chips.push({ label: `${t("records.filter.year")} ${spec.year.min ?? "?"}–${spec.year.max ?? "?"}` });
  if (spec.citation_count && (spec.citation_count.min != null || spec.citation_count.max != null))
    chips.push({ label: `${t("records.filter.citationCount")} ${spec.citation_count.min ?? 0}–${spec.citation_count.max ?? "∞"}` });
  if (spec.doc_type?.length) chips.push({ label: `${t("records.filter.docType")}: ${spec.doc_type.length}` });
  if (spec.language?.length) chips.push({ label: `${t("records.filter.language")}: ${spec.language.length}` });
  if (spec.db_source?.length) chips.push({ label: `${t("records.filter.dbSource")}: ${spec.db_source.length}` });
  if (spec.journal?.length) chips.push({ label: `${t("records.filter.journal")}: ${spec.journal.length}` });
  if (spec.authors?.length) chips.push({ label: `${t("records.filter.authors")}: ${spec.authors.length}` });
  if (spec.wc_categories?.length) chips.push({ label: `WC: ${spec.wc_categories.length}` });
  if (spec.sc_categories?.length) chips.push({ label: `SC: ${spec.sc_categories.length}` });
  if (spec.fulltext?.query) chips.push({ label: `${t("common.search")}: "${spec.fulltext.query.slice(0, 20)}${spec.fulltext.query.length > 20 ? "…" : ""}"` });
  if (spec.quality?.has?.length) chips.push({ label: `${t("records.filter.qualityHas").toLowerCase()}: ${spec.quality.has.join(",")}` });
  if (spec.quality?.missing?.length) chips.push({ label: `${t("records.filter.qualityMissing").toLowerCase()}: ${spec.quality.missing.join(",")}` });

  if (chips.length === 0) return null;
  return (
    <div className="flex items-center gap-1 flex-wrap">
      {chips.map((c, i) => (
        <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-brand-50 text-brand-700 border border-brand-200">
          {c.label}
        </span>
      ))}
    </div>
  );
}

/**
 * Records sayfasından sonraki adım kararı:
 * 1. AI Asistan (opsiyonel — semantik temizlik istersen)
 * 2. Direkt Export (Veri Dönüşümü) — dataset hazır
 *
 * Pipeline 5 adım kalır; 4. adım (AI) opsiyoneldir.
 */
function NextStepCard({ projectId }: { projectId: string }) {
  const t = useT();
  return (
    <Card className="overflow-hidden">
      <CardBody className="p-0">
        <div className="px-5 py-3 border-b border-border bg-gradient-to-r from-cyan-50/70 via-cyan-50/40 to-emerald-50/40">
          <h3 className="font-semibold text-sm text-ink">{t("records.nextStepTitle")}</h3>
          <p className="text-xs text-muted mt-0.5">{t("records.nextStepSubtitle")}</p>
        </div>
        <div className="grid md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-border">
          {/* AI Assistant — Opsiyonel (cyan = brand vurgusu) */}
          <Link
            href={`/projects/${projectId}/enrich`}
            className="group block px-5 py-4 hover:bg-cyan-50/40 transition"
          >
            <div className="flex items-start gap-3">
              <span className="w-10 h-10 rounded-lg bg-gradient-to-br from-cyan-100 to-cyan-200 text-cyan-700 flex items-center justify-center flex-shrink-0">
                <Sparkles className="h-5 w-5" />
              </span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-semibold text-sm text-ink">{t("records.nextStepAITitle")}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-cyan-100 text-cyan-700 font-medium uppercase tracking-wide">
                    {t("records.nextStepAIBadge")}
                  </span>
                </div>
                <p className="text-xs text-muted mt-1 leading-relaxed">
                  {t("records.nextStepAIBody")}
                </p>
                <div className="mt-2.5 flex items-center gap-1 text-xs font-medium text-cyan-700 group-hover:text-cyan-900">
                  {t("records.nextStepAIBtn")}
                  <ArrowRight className="h-3.5 w-3.5 group-hover:translate-x-0.5 transition-transform" />
                </div>
              </div>
            </div>
          </Link>
          {/* Direkt Export */}
          <Link
            href={`/projects/${projectId}/export`}
            className="group block px-5 py-4 hover:bg-emerald-50/40 transition"
          >
            <div className="flex items-start gap-3">
              <span className="w-10 h-10 rounded-lg bg-gradient-to-br from-emerald-100 to-emerald-200 text-emerald-700 flex items-center justify-center flex-shrink-0">
                <FileOutput className="h-5 w-5" />
              </span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-semibold text-sm text-ink">{t("records.nextStepExportTitle")}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-100 text-emerald-700 font-medium uppercase tracking-wide flex items-center gap-0.5">
                    <SkipForward className="h-2.5 w-2.5" />
                    {t("records.nextStepExportBadge")}
                  </span>
                </div>
                <p className="text-xs text-muted mt-1 leading-relaxed">
                  {t("records.nextStepExportBody")}
                </p>
                <div className="mt-2.5 flex items-center gap-1 text-xs font-medium text-emerald-700 group-hover:text-emerald-900">
                  {t("records.nextStepExportBtn")}
                  <ArrowRight className="h-3.5 w-3.5 group-hover:translate-x-0.5 transition-transform" />
                </div>
              </div>
            </div>
          </Link>
        </div>
      </CardBody>
    </Card>
  );
}
