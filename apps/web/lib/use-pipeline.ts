"use client";
import { useCallback, useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Combine, FilterX, Brain, FileOutput, ScrollText } from "lucide-react";
import { api } from "@/lib/api-client";

export type PipelineStep = {
  slug: string;
  labelKey: string;
  icon: typeof Combine;
};

/** Pipeline steps — the shared source for ProjectNav (stepper) + StepNav (next/prev). */
export const PIPELINE_STEPS: PipelineStep[] = [
  { slug: "merge",   labelKey: "nav.analyses", icon: Combine },
  { slug: "records", labelKey: "nav.records",  icon: FilterX },
  { slug: "enrich",  labelKey: "nav.ai",       icon: Brain },
  { slug: "export",  labelKey: "nav.export",   icon: FileOutput },
  { slug: "report",  labelKey: "nav.report",   icon: ScrollText },
];

/** localStorage key — the "Advanced Editing" lock. Tied to the ACTIVE ANALYSIS:
 * a new merge (new analysis id) → the lock is reset automatically; old
 * (analysis-independent) stuck flags are ignored. */
export const advancedKey = (id: string, analysisId: string) => `bibexpy_adv_${id}_${analysisId}`;

/**
 * Pipeline gating state — shared by ProjectNav (stepper) and StepNav (next/prev):
 *   merge          : always enabled
 *   export / report: if a merge has been done
 *   records / enrich: if merge is done + "Advanced Editing" has been enabled
 * Pulled from mergeSummary + localStorage; refreshed on page change + on the
 * 'bibexpy:status' event.
 */
export function usePipelineGating(id: string) {
  const pathname = usePathname();
  const [merged, setMerged] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    if (!id) return;
    try {
      const ms = await api.mergeSummary(id);
      const hasMerge = !!ms?.has_merge;
      setMerged(hasMerge);
      // The "Advanced Editing" lock is read per the active analysis — if there is no
      // merge / no analysis id it is off (records/enrich locked).
      const analysisId = ms?.analysis?.id;
      let adv = false;
      if (hasMerge && analysisId) {
        try { adv = localStorage.getItem(advancedKey(id, analysisId)) === "1"; } catch { /* ignore */ }
      }
      setAdvanced(adv);
    } catch {
      /* network error — do not force the locks (if loaded stays false, everything appears open) */
    } finally {
      setLoaded(true);
    }
  }, [id]);

  useEffect(() => { refresh(); }, [refresh, pathname]);

  useEffect(() => {
    const h = () => refresh();
    window.addEventListener("bibexpy:status", h);
    return () => window.removeEventListener("bibexpy:status", h);
  }, [refresh]);

  const isEnabled = useCallback((slug: string): boolean => {
    if (!loaded) return true; // do not flash the locks until loaded
    switch (slug) {
      case "merge": return true;
      case "export":
      case "report": return merged;
      case "records":
      case "enrich": return merged && advanced;
      default: return true;
    }
  }, [loaded, merged, advanced]);

  return { merged, advanced, loaded, isEnabled, refresh };
}
