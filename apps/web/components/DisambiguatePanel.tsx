"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import {
  api, type Cluster, type AuthorSplit, type DisambiguationStatus,
  type ProposalSet, translateApiError,
} from "@/lib/api-client";
import { Card, CardBody } from "./Card";
import { Button } from "./Button";
import { JobProgress } from "./JobProgress";
import {
  AlertTriangle, Sparkles, ShieldQuestion,
  SplitSquareHorizontal, Combine,
} from "lucide-react";
import { useT } from "@/lib/i18n";
import { useConfirm, useToast } from "./Dialogs";

type Kind = "authors" | "affiliations" | "countries" | "organizations";

/**
 * Yazar Ayrıştırma paneli — tek "Tara" akışı, iki tür öneri:
 *   • Ayrıştırma (split): aynı isim, alan-ayrık → farklı kişiler → (b)(c) eki
 *   • Birleştirme (merge): farklı yazılış, aynı kişi → tek standart yazılış
 * Tier 1 (deterministik) önerileri ön-işaretli gelir; Tier 2 (AI/sınırda)
 * işaretsiz. Hepsi kullanıcı onayına tabi.
 */
export function DisambiguatePanel({
  projectId, kind = "authors",
}: { projectId: string; kind?: Kind }) {
  const t = useT();
  const confirm = useConfirm();
  const toast = useToast();
  const [status, setStatus] = useState<DisambiguationStatus | null>(null);
  const [proposals, setProposals] = useState<ProposalSet | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [approvedMerges, setApprovedMerges] = useState<Set<string>>(new Set());
  const [approvedSplits, setApprovedSplits] = useState<Set<string>>(new Set());
  const [canonicalEdits, setCanonicalEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => { api.disambiguateStatus(projectId).then(setStatus).catch(() => {}); }, [projectId]);

  function applyProposalSet(p: ProposalSet) {
    setProposals(p);
    // Tier 1 (deterministik) önerileri ön-işaretle — ama yalnız GÖRÜNÜR (≥2 varyant)
    // kümeleri; gizli tek-varyant kümeler pre-check edilmez (yoksa sayım/uygulama desenk).
    const visible = (p.clusters || []).filter(
      (c) => c.members.length >= 2 || clusterVariants(c, kind).length >= 2,
    );
    setApprovedMerges(new Set(visible.filter((c) => c.source === "deterministic").map((c) => c.cluster_id)));
    setApprovedSplits(new Set((p.splits || []).filter((s) => s.source === "deterministic").map((s) => s.split_id)));
    setCanonicalEdits({});
  }

  async function loadProposals() {
    try {
      applyProposalSet(await api.getProposals(projectId, kind));
    } catch (e) {
      setError(translateApiError(t, e));
    }
  }

  // kind/proje değişince: eski önerileri HEMEN temizle ve yalnız GÜNCEL kind'in
  // yanıtını uygula. Bayat (yavaş gelen önceki kind) yanıtını yok say — yoksa ilk
  // yüklenen authors yanıtı sonradan gelip içeriği hep authors'a sabitler.
  useEffect(() => {
    let alive = true;
    setProposals(null);
    setError(null);
    setJobId(null);
    setApprovedMerges(new Set());
    setApprovedSplits(new Set());
    setCanonicalEdits({});
    api.getProposals(projectId, kind)
      .then((p) => { if (alive) applyProposalSet(p); })
      .catch((e) => { if (alive) setError(translateApiError(t, e)); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, projectId]);

  async function start() {
    setError(null); setBusy(true);
    try {
      const starter =
        kind === "authors" ? api.startAuthorDisambiguation
        : kind === "affiliations" ? api.startAffiliationDisambiguation
        : kind === "countries" ? api.startCountryStandardization
        : api.startOrgRollup;
      const { job_id } = await starter(projectId, "auto");
      setJobId(job_id);
    } catch (e) {
      setError(translateApiError(t, e, "disambiguate.startFailed"));
    } finally { setBusy(false); }
  }

  const splits = (kind === "authors" ? proposals?.splits : []) || [];
  // Tek varyantlı/tek üyeli kümeleri gizle — birleştirilecek bir şey yok (boş kart olmasın).
  // apply YALNIZ bu görünür listeyi kullanır; gizli küme sessizce uygulanmaz.
  const clusters = (proposals?.clusters || []).filter(
    (c) => c.members.length >= 2 || clusterVariants(c, kind).length >= 2,
  );

  const totalApproved = approvedMerges.size + approvedSplits.size;

  function canonicalFor(c: Cluster): string | undefined {
    const vs = clusterVariants(c, kind);  // name_variants veya variants → id fallback
    return canonicalEdits[c.cluster_id] || c.canonical_name || vs[0];
  }

  async function applyApproved() {
    if (totalApproved === 0) return;
    if (!(await confirm({ message: t("disambiguate.applyConfirm", { n: totalApproved }) }))) return;
    setBusy(true); setError(null);
    try {
      // YALNIZ görünür (filtrelenmiş) kümelerden onaylananları uygula — gizli tek-varyant
      // kümeler (ör. tek yazılışlı ülke/kurum) sessizce uygulanmaz.
      const mergeItems = clusters
        .filter((c) => approvedMerges.has(c.cluster_id))
        .map((c) => ({ cluster_id: c.cluster_id, canonical: canonicalFor(c) }));
      const splitItems = splits
        .filter((s) => approvedSplits.has(s.split_id))
        .map((s) => ({ split_id: s.split_id }));
      const r = await api.applyClusters(projectId, kind, mergeItems, splitItems);
      toast(t("disambiguate.appliedMessage", { n: r.replacements, snapshot: r.snapshot ?? "—" }), { tone: "success" });
      await loadProposals();
    } catch (e) {
      setError(translateApiError(t, e, "disambiguate.applyFailed"));
    } finally { setBusy(false); }
  }

  const subject = t(`disambiguate.subjects.${kind}`);

  return (
    <div className="space-y-5">
      {status && status.configured && (
        <div className="rounded-lg border border-info/30 bg-info-soft/50 px-4 py-2 text-xs text-blue-900 flex items-center gap-2">
          <ShieldQuestion className="h-3.5 w-3.5 flex-shrink-0" />
          <span>
            {t("disambiguate.providerLabel")}: <code className="font-mono bg-white px-1 rounded">{status.provider ?? "?"}</code>
            {" · "}{t("disambiguate.modelLabel")}: <code className="font-mono bg-white px-1 rounded">{status.model}</code>
          </span>
          <Link href="/settings" className="ml-auto text-brand-600 hover:underline whitespace-nowrap">
            {t("disambiguate.changeLink")}
          </Link>
        </div>
      )}
      {status && !status.configured && (
        <div className="rounded-lg border border-warning/40 bg-warning-soft px-4 py-2 text-sm text-amber-900 flex gap-2">
          <AlertTriangle className="h-4 w-4 mt-0.5" />
          <div>
            {t("disambiguate.llmNotConfigured")}{" "}
            <Link href="/settings" className="underline font-medium">{t("disambiguate.settingsLink")}</Link>
            {t("disambiguate.settingsHintAfter")}
          </div>
        </div>
      )}

      {error && <div className="rounded-lg border border-danger/30 bg-danger-soft px-4 py-2 text-sm text-red-700">{error}</div>}

      <p className="text-xs text-muted">{kind === "authors" ? t("disambiguate.scanHint") : t("disambiguate.scanHintStd", { subject })}</p>

      {/* Tara */}
      <div className="flex items-center gap-2 flex-wrap">
        <Button onClick={start} disabled={busy || !!jobId} size="sm">
          <Sparkles className="h-4 w-4" /> {t("disambiguate.scan")}
        </Button>
        <Button onClick={loadProposals} variant="secondary" size="sm">{t("common.refresh")}</Button>
        {totalApproved > 0 && (
          <Button onClick={applyApproved} disabled={busy} variant="success" size="sm" className="ml-auto">
            {t("disambiguate.approveApply", { n: totalApproved })}
          </Button>
        )}
      </div>

      {jobId && <JobProgress jobId={jobId} onComplete={() => { loadProposals(); setJobId(null); }} onClose={() => setJobId(null)} />}

      {/* Ayrıştırma (split) — yalnız authors */}
      {kind === "authors" && (
        <section className="space-y-2">
          <div className="flex items-center gap-2">
            <SplitSquareHorizontal className="h-4 w-4 text-brand-600" />
            <h3 className="font-semibold text-sm">{t("disambiguate.splitHeader")}</h3>
            <span className="text-xs text-muted">({splits.length})</span>
            {splits.length > 0 && (
              <button
                onClick={() => {
                  const allSelected = splits.every((s) => approvedSplits.has(s.split_id));
                  setApprovedSplits(allSelected ? new Set() : new Set(splits.map((s) => s.split_id)));
                }}
                className="ml-auto text-[11px] font-medium text-brand-600 hover:text-brand-700 hover:underline"
              >
                {splits.every((s) => approvedSplits.has(s.split_id)) ? t("disambiguate.clearAll") : t("disambiguate.selectAll")}
              </button>
            )}
          </div>
          <p className="text-[11px] text-muted">{t("disambiguate.splitDesc")}</p>
          {splits.length === 0 ? (
            <p className="text-xs text-muted py-2">{t("disambiguate.noSplits")}</p>
          ) : splits.map((s) => (
            <SplitRow key={s.split_id} split={s} approved={approvedSplits.has(s.split_id)}
              onToggle={() => {
                const n = new Set(approvedSplits);
                n.has(s.split_id) ? n.delete(s.split_id) : n.add(s.split_id);
                setApprovedSplits(n);
              }} />
          ))}
        </section>
      )}

      {/* Birleştirme (merge) */}
      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <Combine className="h-4 w-4 text-brand-600" />
          <h3 className="font-semibold text-sm">{t("disambiguate.mergeHeader", { subject })}</h3>
          <span className="text-xs text-muted">({clusters.length})</span>
          {clusters.length > 0 && (
            <button
              onClick={() => {
                const allSelected = clusters.every((c) => approvedMerges.has(c.cluster_id));
                setApprovedMerges(allSelected ? new Set() : new Set(clusters.map((c) => c.cluster_id)));
              }}
              className="ml-auto text-[11px] font-medium text-brand-600 hover:text-brand-700 hover:underline"
            >
              {clusters.every((c) => approvedMerges.has(c.cluster_id)) ? t("disambiguate.clearAll") : t("disambiguate.selectAll")}
            </button>
          )}
        </div>
        <p className="text-[11px] text-muted">{t("disambiguate.mergeDesc", { subject })}</p>
        {clusters.length === 0 ? (
          <p className="text-xs text-muted py-2">{t("disambiguate.noMerges")}</p>
        ) : clusters.map((c, i) => (
          <MergeRow key={`${c.cluster_id}-${i}`} cluster={c} kind={kind} approved={approvedMerges.has(c.cluster_id)}
            canonical={canonicalEdits[c.cluster_id]}
            onToggle={() => {
              const n = new Set(approvedMerges);
              n.has(c.cluster_id) ? n.delete(c.cluster_id) : n.add(c.cluster_id);
              setApprovedMerges(n);
            }}
            onCanonical={(v) => setCanonicalEdits({ ...canonicalEdits, [c.cluster_id]: v })} />
        ))}
      </section>
    </div>
  );
}

/** Tier rozeti — otomatik (deterministik) veya incele (LLM/sınırda). */
function TierBadge({ tier, source }: { tier?: number; source?: string }) {
  const t = useT();
  const auto = tier === 1 || source === "deterministic";
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${auto ? "bg-success-soft text-emerald-700" : "bg-warning-soft text-amber-700"}`}>
      {auto ? t("disambiguate.autoBadge") : t("disambiguate.reviewBadge")}
    </span>
  );
}

function SplitRow({ split, approved, onToggle }: { split: AuthorSplit; approved: boolean; onToggle: () => void }) {
  const t = useT();
  return (
    <Card className={approved ? "ring-2 ring-brand-500" : ""}>
      <CardBody>
        <div className="flex items-start gap-3">
          <input type="checkbox" checked={approved} onChange={onToggle} className="mt-1 h-4 w-4 accent-brand-500" />
          <div className="flex-1 min-w-0 space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-semibold text-sm">{split.name}</span>
              <TierBadge tier={split.tier} source={split.source} />
            </div>
            <div className="space-y-1">
              {split.groups.map((g, gi) => (
                <div key={gi} className="flex items-center gap-2 text-xs">
                  <span className="font-mono font-semibold text-brand-700 min-w-[5.5rem]">
                    {g.suffix ? `${split.name} ${g.suffix}` : `${split.name}`}
                  </span>
                  {!g.suffix && <span className="text-[10px] text-muted">({t("disambiguate.plainStays")})</span>}
                  <span className="text-muted truncate">
                    {g.fields.slice(0, 4).join(", ")}{g.fields.length > 4 ? "…" : ""}
                    <span className="opacity-60"> · {g.records.length} {t("common.records")}</span>
                  </span>
                </div>
              ))}
            </div>
            <p className="text-[11px] text-muted">{split.reason}</p>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

/** Bir kümenin birleştirdiği farklı varyantlar; varyant yoksa üye id'sine düşer. */
function clusterVariants(c: Cluster, kind: Kind): string[] {
  const out: string[] = [];
  for (const m of c.members) {
    const arr = (kind === "authors" ? m.name_variants : m.variants) ?? [];
    if (arr.length) out.push(...arr);
    else if (m.id) out.push(m.id);
  }
  return [...new Set(out)];
}

function MergeRow({ cluster: c, kind, approved, canonical, onToggle, onCanonical }: {
  cluster: Cluster; kind: Kind; approved: boolean; canonical?: string;
  onToggle: () => void; onCanonical: (v: string) => void;
}) {
  const t = useT();
  const uniq = clusterVariants(c, kind);
  const canon = canonical || c.canonical_name || uniq[0] || "";
  return (
    <Card className={approved ? "ring-2 ring-brand-500" : ""}>
      <CardBody>
        <div className="flex items-start gap-3">
          <input type="checkbox" checked={approved} onChange={onToggle} className="mt-1 h-4 w-4 accent-brand-500" />
          <div className="flex-1 min-w-0 space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              <TierBadge tier={c.tier} source={c.source} />
              {c.source !== "deterministic" && (
                <span className="text-xs px-2 py-0.5 rounded-full bg-bg-soft text-muted">
                  {(c.confidence * 100).toFixed(0)}% {t("disambiguate.confidenceLabel")}
                </span>
              )}
              {c.country && <span className="text-xs text-muted">{c.country}</span>}
            </div>
            <div className="flex flex-wrap gap-1">
              {uniq.slice(0, 12).map((v) => (
                <span key={v} className="text-xs px-2 py-0.5 rounded bg-bg-soft border border-border">{v}</span>
              ))}
              {uniq.length > 12 && <span className="text-xs text-muted">+{uniq.length - 12}</span>}
            </div>
            <p className="text-[11px] text-muted">{c.reason}</p>
            <div className="flex items-center gap-2">
              <label className="text-[11px] text-muted">{t("disambiguate.canonicalLabel")}</label>
              <input value={canon} onChange={(e) => onCanonical(e.target.value)}
                className="flex-1 rounded-md border border-border bg-white px-2 py-1 text-xs" />
            </div>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}
