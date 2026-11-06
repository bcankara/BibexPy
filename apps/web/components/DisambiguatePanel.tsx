"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  api, type Cluster, type AuthorSplit, type DisambiguationStatus,
  type JobInfo, type ProposalSet, translateApiError, translateTitle,
} from "@/lib/api-client";
import {
  isBusyError, refreshProjectJobs, start as startTrackedJob, useJobFinished, useProjectBusy, useProjectJobs,
} from "@/lib/job-store";
import { Card, CardBody } from "./Card";
import { Button } from "./Button";
import { JobProgress } from "./JobProgress";
import {
  AlertTriangle, Sparkles, ShieldQuestion,
  SplitSquareHorizontal, Combine, Loader2,
} from "lucide-react";
import { useI18n, useT } from "@/lib/i18n";
import { formatPercent } from "@/lib/i18n/format";
import { useConfirm, useToast } from "./Dialogs";
import { cn } from "@/lib/cn";
import { LOCKED_CLASS } from "./BlockingOverlay";

type Kind = "authors" | "affiliations" | "countries" | "organizations";

/**
 * Does a job's backend `kind` belong to the `Kind` the panel is showing? The backend
 * uses separate job.kind values for authors/affiliations ("disambiguate_authors" /
 * "disambiguate_affiliations"); countries and organizations, however, share a SINGLE
 * common job.kind ("disambiguate") (see apps/api/routers/disambiguate.py) — the two
 * cannot be told apart in the backend, and since only one exclusive job can run per
 * project at a time anyway, this is sufficient.
 */
function jobMatchesKind(job: JobInfo, kind: Kind): boolean {
  if (kind === "authors") return job.kind === "disambiguate_authors";
  if (kind === "affiliations") return job.kind === "disambiguate_affiliations";
  return job.kind === "disambiguate";
}

/**
 * Author Disambiguation panel — a single "Scan" flow, two kinds of suggestions:
 *   • Split: same name, disjoint fields → different people → (b)(c) suffix
 *   • Merge: different spellings, same person → a single standard spelling
 * Tier 1 (deterministic) suggestions come pre-checked; Tier 2 (AI/borderline)
 * come unchecked. All of them are subject to user approval.
 */
export function DisambiguatePanel({
  projectId, kind = "authors",
}: { projectId: string; kind?: Kind }) {
  const t = useT();
  const confirm = useConfirm();
  const toast = useToast();
  const [status, setStatus] = useState<DisambiguationStatus | null>(null);
  const [proposals, setProposals] = useState<ProposalSet | null>(null);
  // Job started from THIS panel instance (kept so a failed run's card stays visible).
  const [startedJobId, setStartedJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The backend refused to apply the stored proposals (409 proposals_stale_rescan): they
  // were produced for an older dataset state or format. Only a new scan clears this.
  const [stale, setStale] = useState(false);
  // A scan that is already running (started here earlier, before a navigation, or from
  // another tab) is picked up from the shared job store — no local re-attach code needed.
  const { active: activeJobs } = useProjectJobs(projectId);
  const attachedJob = activeJobs.find((j) => jobMatchesKind(j, kind));
  const jobId = startedJobId ?? attachedJob?.id ?? null;
  // Another exclusive job (e.g. "Fill all missing") holds the project: a scan would be refused.
  const blockingJob = activeJobs.find((j) => j.exclusive && !jobMatchesKind(j, kind));
  const setJobId = setStartedJobId;
  // ANY exclusive job of the project (also a placeholder that was just started) locks scan and apply.
  const { busy: jobBusy } = useProjectBusy(projectId);
  const busyTitle = jobBusy ? t("lock.busyReason") : undefined;
  const [approvedMerges, setApprovedMerges] = useState<Set<string>>(new Set());
  const [approvedSplits, setApprovedSplits] = useState<Set<string>>(new Set());
  const [canonicalEdits, setCanonicalEdits] = useState<Record<string, string>>({});
  // Three separate buttons (Scan / Refresh / Approve & apply), three separate in-flight states —
  // each shows its own spinner and is independently guarded against re-entry.
  const [scanning, setScanning] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [applying, setApplying] = useState(false);

  useEffect(() => { api.disambiguateStatus(projectId).then(setStatus).catch(() => {}); }, [projectId]);

  function applyProposalSet(p: ProposalSet) {
    setProposals(p);
    // Pre-check Tier 1 (deterministic) suggestions — but only the VISIBLE (≥2 variants)
    // clusters; hidden single-variant clusters are not pre-checked (otherwise the
    // count and apply go out of sync).
    const visible = (p.clusters || []).filter(
      (c) => c.members.length >= 2 || clusterVariants(c, kind).length >= 2,
    );
    setApprovedMerges(new Set(visible.filter((c) => c.source === "deterministic").map((c) => c.cluster_id)));
    setApprovedSplits(new Set((p.splits || []).filter((s) => s.source === "deterministic").map((s) => s.split_id)));
    // A stored proposal file that is not schema uid_v2 (written before row UIDs existed) cannot
    // be applied: the backend would refuse it with 409. Show the re-scan banner up front.
    // Only ever set here, never cleared: a scan / kind change clears it, so a refresh cannot
    // hide a staleness the backend reported (e.g. a proposal file of another analysis).
    if (((p.clusters?.length ?? 0) + (p.splits?.length ?? 0)) > 0 && p.schema !== "uid_v2") setStale(true);
    setCanonicalEdits({});
  }

  async function loadProposals() {
    if (refreshing) return;
    setRefreshing(true);
    try {
      applyProposalSet(await api.getProposals(projectId, kind));
    } catch (e) {
      setError(translateApiError(t, e));
    } finally {
      setRefreshing(false);
    }
  }
  // The job-finished listener below must call the CURRENT loadProposals (it closes over kind).
  const loadProposalsRef = useRef(loadProposals);
  loadProposalsRef.current = loadProposals;

  // When kind/project changes: clear the old suggestions IMMEDIATELY and apply only the
  // response for the CURRENT kind. Ignore a stale response (a slow response for the previous
  // kind) — otherwise the first-loaded authors response would arrive later and pin the
  // content to authors.
  useEffect(() => {
    let alive = true;
    setProposals(null);
    setError(null);
    setStale(false);
    setJobId(null);
    setApprovedMerges(new Set());
    setApprovedSplits(new Set());
    setCanonicalEdits({});
    api.getProposals(projectId, kind)
      .then((p) => { if (alive) applyProposalSet(p); })
      .catch((e) => { if (alive) setError(translateApiError(t, e)); });
    // Re-attaching to an in-progress scan (the user navigated away and came back) is done by
    // the shared job store: `useProjectJobs` above finds the queued/running job of this kind.
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, projectId]);

  // Reload the suggestions whenever a scan of this kind ends — also for a scan the panel
  // re-attached to (its progress card unmounts as soon as the job is no longer active).
  useJobFinished((e) => {
    if (e.job.project_id === projectId && jobMatchesKind(e.job, kind) && e.outcome !== "lost") {
      void loadProposalsRef.current();
    }
  });

  async function start() {
    if (scanning || jobId || jobBusy) return;
    setError(null); setStale(false); setScanning(true);
    try {
      const starter =
        kind === "authors" ? api.startAuthorDisambiguation
        : kind === "affiliations" ? api.startAffiliationDisambiguation
        : kind === "countries" ? api.startCountryStandardization
        : api.startOrgRollup;
      setJobId(await startTrackedJob(projectId, starter(projectId, "auto")));
    } catch (e) {
      setError(translateApiError(t, e, "disambiguate.startFailed"));
      // 409: a job is already running (maybe not known to this page yet) — pick it up.
      if (isBusyError(e)) void refreshProjectJobs(projectId);
    } finally { setScanning(false); }
  }

  const splits = (kind === "authors" ? proposals?.splits : []) || [];
  // Hide single-variant/single-member clusters — there is nothing to merge (avoid empty cards).
  // apply uses ONLY this visible list; a hidden cluster is never silently applied.
  const clusters = (proposals?.clusters || []).filter(
    (c) => c.members.length >= 2 || clusterVariants(c, kind).length >= 2,
  );

  const totalApproved = approvedMerges.size + approvedSplits.size;

  function canonicalFor(c: Cluster): string | undefined {
    const vs = clusterVariants(c, kind);  // name_variants or variants → id fallback
    return canonicalEdits[c.cluster_id] || c.canonical_name || vs[0];
  }

  async function applyApproved() {
    if (totalApproved === 0 || applying || jobBusy) return;
    if (!(await confirm({ message: t("disambiguate.applyConfirm", { n: totalApproved }) }))) return;
    setApplying(true); setError(null);
    try {
      // Apply ONLY the approved ones from the visible (filtered) clusters — hidden single-variant
      // clusters (e.g. a country/organization with a single spelling) are never silently applied.
      const mergeItems = clusters
        .filter((c) => approvedMerges.has(c.cluster_id))
        .map((c) => ({ cluster_id: c.cluster_id, canonical: canonicalFor(c) }));
      const splitItems = splits
        .filter((s) => approvedSplits.has(s.split_id))
        .map((s) => ({ split_id: s.split_id }));
      const r = await api.applyClusters(projectId, kind, mergeItems, splitItems);
      toast(t("disambiguate.appliedMessage", { n: r.replacements, snapshot: r.snapshot ?? "—" }), { tone: "success" });
      // Records of the proposal that no longer exist in the dataset are skipped by the
      // backend (never remapped to another row) — tell the user how many.
      if ((r.skipped_missing ?? 0) > 0) {
        toast(t("disambiguate.skippedMissing", { n: r.skipped_missing ?? 0 }), { tone: "warning" });
      }
      await loadProposals();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "";
      if (/proposals_stale_rescan/.test(msg)) setStale(true);
      else setError(translateApiError(t, e, "disambiguate.applyFailed"));
    } finally { setApplying(false); }
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

      {stale && (
        <div className="rounded-lg border border-warning/40 bg-warning-soft px-4 py-3 text-sm text-amber-900 flex items-center gap-3 flex-wrap">
          <AlertTriangle className="h-4 w-4 flex-shrink-0" />
          <span className="flex-1 min-w-[16rem]">{t("disambiguate.staleRescan")}</span>
          <Button onClick={start} disabled={scanning || !!jobId || !!blockingJob || jobBusy} title={busyTitle} size="sm" className={LOCKED_CLASS}>
            {scanning ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
            {t("disambiguate.rescan")}
          </Button>
        </div>
      )}

      <p className="text-xs text-muted">{kind === "authors" ? t("disambiguate.scanHint") : t("disambiguate.scanHintStd", { subject })}</p>

      {blockingJob && (
        <div className="rounded-lg border border-cyan-200 bg-cyan-50 px-4 py-2 text-xs text-cyan-900 flex items-center gap-2">
          <Loader2 className="h-3.5 w-3.5 flex-shrink-0 animate-spin" />
          {t("jobs.busyNotice", { title: translateTitle(t, blockingJob) || t("jobs.running") })}
        </div>
      )}

      {/* Scan */}
      <div className="flex items-center gap-2 flex-wrap">
        <Button onClick={start} disabled={scanning || !!jobId || !!blockingJob || jobBusy} title={busyTitle} size="sm" className={LOCKED_CLASS}>
          {scanning ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
          {t("disambiguate.scan")}
        </Button>
        <Button onClick={loadProposals} disabled={refreshing} variant="secondary" size="sm">
          {refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {t("common.refresh")}
        </Button>
        {totalApproved > 0 && !stale && (
          <Button onClick={applyApproved} disabled={applying || jobBusy || activeJobs.some((j) => j.exclusive)} title={busyTitle} variant="success" size="sm" className={cn("ml-auto", LOCKED_CLASS)}>
            {applying ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {t("disambiguate.approveApply", { n: totalApproved })}
          </Button>
        )}
      </div>

      {jobId && (
        <JobProgress
          jobId={jobId}
          // A finished or cancelled run closes its card (suggestions reload via the finish
          // listener above); a FAILED run keeps it open so the translated error stays visible.
          onComplete={(j) => { if (j.status !== "failed") setJobId(null); }}
          onClose={() => setJobId(null)}
        />
      )}

      {/* Split — authors only */}
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

      {/* Merge */}
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

/** Tier badge — auto (deterministic) or review (LLM/borderline). */
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

/** The distinct variants a cluster merges; falls back to the member id if there are no variants. */
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
  const { locale } = useI18n();
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
                  {formatPercent(c.confidence * 100, locale, 0)} {t("disambiguate.confidenceLabel")}
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
