"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/cn";
import { Lock, Check } from "lucide-react";
import { useT } from "@/lib/i18n";
import { useProjectId } from "@/lib/use-project-id";
import { PIPELINE_STEPS, usePipelineGating } from "@/lib/use-pipeline";

// Geriye uyumluluk — advancedKey artık lib/use-pipeline'da.
export { advancedKey } from "@/lib/use-pipeline";

/**
 * Pipeline stepper — wizard görünümlü, gating'li (Burak: "daha görünür/şık,
 * takip edilebilir"). 5 adım, numaralı/✓/kilit daireler + bağlayıcı çizgiler:
 *   1 Veri & Birleştirme        : her zaman açık
 *   2 Records / 3 Harmonizasyon : Birleştir + "Gelişmiş Düzenleme" açıldıysa (kilitli)
 *   4 Export / 5 Report         : Birleştir tamamlandıysa
 * Geçilen adımlar ✓, aktif vurgulu, kilitli soluk + tıklanamaz. Gating durumu
 * lib/use-pipeline (usePipelineGating) ile StepNav'le paylaşılır.
 */
export function ProjectNav() {
  const id = useProjectId();
  const pathname = usePathname();
  const t = useT();
  const { isEnabled } = usePipelineGating(id);

  const onProjectStep = !!pathname && /^\/projects\/[^/]+\/[^/]+/.test(pathname);
  if (!onProjectStep) return null;

  function lockHintKey(slug: string): string {
    if (slug === "records" || slug === "enrich") return "nav.lockNeedAdvanced";
    return "nav.lockNeedMerge"; // export / report
  }

  const activeIndex = PIPELINE_STEPS.findIndex((s) => pathname?.startsWith(`/projects/${id}/${s.slug}`));

  return (
    <nav className="border-b border-border bg-gradient-to-b from-white to-bg-soft/30">
      <ol className="mx-auto flex max-w-6xl items-center px-4 py-3.5 sm:px-6">
        {PIPELINE_STEPS.map((s, i) => {
          const href = `/projects/${id}/${s.slug}`;
          const active = i === activeIndex;
          const enabled = active || isEnabled(s.slug);
          const done = enabled && activeIndex >= 0 && i < activeIndex;
          const Icon = s.icon;
          const isLast = i === PIPELINE_STEPS.length - 1;
          const nextEnabled = !isLast && isEnabled(PIPELINE_STEPS[i + 1].slug);

          const step = (
            <span className="flex items-center gap-2.5">
              <span className={cn(
                "flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full text-sm font-bold transition",
                active ? "bg-brand-500 text-white ring-4 ring-brand-100"
                  : done ? "bg-brand-500 text-white"
                  : enabled ? "border-2 border-border bg-white text-muted group-hover:border-brand-300"
                  : "border-2 border-dashed border-border bg-bg-soft text-muted/40",
              )}>
                {done ? <Check className="h-4 w-4" /> : !enabled ? <Lock className="h-3.5 w-3.5" /> : i + 1}
              </span>
              <span className="hidden items-center gap-1.5 sm:flex">
                <Icon className={cn(
                  "h-4 w-4 flex-shrink-0",
                  active ? "text-brand-600" : done ? "text-brand-500" : enabled ? "text-muted" : "text-muted/40",
                )} />
                <span className={cn(
                  "whitespace-nowrap text-[13px] font-medium leading-tight",
                  active ? "font-bold text-brand-700" : enabled ? "text-ink" : "text-muted/40",
                )}>{t(s.labelKey)}</span>
              </span>
            </span>
          );

          return (
            <li key={s.slug} className={cn("flex items-center", isLast ? "flex-initial" : "flex-1")}>
              {enabled ? (
                <Link
                  href={href}
                  aria-current={active ? "step" : undefined}
                  className="group flex-shrink-0 rounded-lg px-1.5 py-1 transition hover:bg-white/70"
                >
                  {step}
                </Link>
              ) : (
                <span
                  title={t(lockHintKey(s.slug))}
                  aria-disabled="true"
                  className="flex-shrink-0 cursor-not-allowed select-none px-1.5 py-1"
                >
                  {step}
                </span>
              )}
              {!isLast && (
                <span className={cn(
                  "mx-2 h-0.5 flex-1 rounded-full transition sm:mx-3",
                  done ? "bg-brand-400" : nextEnabled ? "bg-brand-200" : "bg-border",
                )} />
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
