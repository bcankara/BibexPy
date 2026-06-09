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

/** Pipeline adımları — ProjectNav (stepper) + StepNav (next/prev) ortak kaynağı. */
export const PIPELINE_STEPS: PipelineStep[] = [
  { slug: "merge",   labelKey: "nav.analyses", icon: Combine },
  { slug: "records", labelKey: "nav.records",  icon: FilterX },
  { slug: "enrich",  labelKey: "nav.ai",       icon: Brain },
  { slug: "export",  labelKey: "nav.export",   icon: FileOutput },
  { slug: "report",  labelKey: "nav.report",   icon: ScrollText },
];

/** localStorage anahtarı — "Gelişmiş Düzenleme" kilidi. AKTİF ANALİZE bağlı:
 * yeni merge (yeni analiz id) → kilit otomatik sıfırlanır; eski (analiz-bağımsız)
 * takılı bayraklar yok sayılır. */
export const advancedKey = (id: string, analysisId: string) => `bibexpy_adv_${id}_${analysisId}`;

/**
 * Pipeline gating durumu — ProjectNav (stepper) ve StepNav (next/prev) paylaşır:
 *   merge          : her zaman açık
 *   export / report: merge yapıldıysa
 *   records / enrich: merge + "Gelişmiş Düzenleme" açıldıysa
 * mergeSummary + localStorage'tan çekilir; sayfa değişiminde + 'bibexpy:status'
 * event'inde tazelenir.
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
      // "Gelişmiş Düzenleme" kilidi aktif analize bağlı okunur — merge yoksa /
      // analiz id'si yoksa kapalı (records/enrich kilitli).
      const analysisId = ms?.analysis?.id;
      let adv = false;
      if (hasMerge && analysisId) {
        try { adv = localStorage.getItem(advancedKey(id, analysisId)) === "1"; } catch { /* ignore */ }
      }
      setAdvanced(adv);
    } catch {
      /* ağ hatası — kilitleri zorlama (loaded false kalırsa hepsi açık görünür) */
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
    if (!loaded) return true; // yüklenene kadar kilit-flash gösterme
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
