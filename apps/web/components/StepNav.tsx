"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowLeft, ArrowRight, History } from "lucide-react";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/cn";
import { useProjectId } from "@/lib/use-project-id";
import { PIPELINE_STEPS, usePipelineGating, type PipelineStep } from "@/lib/use-pipeline";

type NextOpt = { href: string; label: string; onClick?: () => void; tone?: "cyan" | "brand" | "emerald" };

const TONE: Record<NonNullable<NextOpt["tone"]>, string> = {
  // "cyan" tonu = ana CTA → kurumsal vurgu rengi petrol (#0f766e).
  cyan: "bg-teal-600 hover:bg-teal-500 ring-teal-300/40",
  brand: "bg-brand-600 hover:bg-brand-500 ring-brand-300/40",
  emerald: "bg-emerald-600 hover:bg-emerald-500 ring-emerald-300/40",
};

/**
 * Header sağ köşesi — History + Önceki/Sonraki butonları (Burak).
 *
 * Önceki: KİLİTLİ adımları ATLAR (en yakın açık önceki adım).
 * Sonraki: sayfa `nextOptions` verdiyse onları gösterir (örn. merge sayfasında
 *   "Download Data" + "Gelişmiş Düzenleme" — kullanıcı hangisine isterse gider);
 *   yoksa gating-aware tek "en yakın açık sonraki adım" (kilitliyi atlar).
 */
export function StepNav({ onHistory, nextOptions }: {
  onHistory?: () => void;
  nextOptions?: NextOpt[] | null;
  // geriye uyumluluk — yok sayılır
  nextHref?: string;
  nextLabel?: string;
}) {
  const id = useProjectId();
  const pathname = usePathname();
  const t = useT();
  const { isEnabled } = usePipelineGating(id);

  const idx = PIPELINE_STEPS.findIndex((s) => pathname?.includes(`/projects/${id}/${s.slug}`));

  let prev: PipelineStep | null = null;
  for (let i = idx - 1; i >= 0; i--) {
    if (isEnabled(PIPELINE_STEPS[i].slug)) { prev = PIPELINE_STEPS[i]; break; }
  }

  // Sonraki seçenekler: sayfa verdiyse onları kullan; yoksa gating-aware tek sonraki.
  let options: NextOpt[] = nextOptions ?? [];
  if (options.length === 0 && idx >= 0) {
    for (let i = idx + 1; i < PIPELINE_STEPS.length; i++) {
      if (isEnabled(PIPELINE_STEPS[i].slug)) {
        options = [{ href: `/projects/${id}/${PIPELINE_STEPS[i].slug}`, label: t(PIPELINE_STEPS[i].labelKey), tone: "cyan" }];
        break;
      }
    }
  }

  const single = options.length === 1;
  const hasNav = idx >= 0 && (prev || options.length > 0);

  return (
    <div className="flex items-stretch gap-2.5">
      {onHistory && (
        <button
          onClick={onHistory}
          title={t("common.history")}
          className="flex flex-col items-center justify-center gap-1 rounded-xl bg-white/10 px-4 text-white ring-1 ring-white/15 transition hover:bg-white/20"
        >
          <History className="h-6 w-6" />
          <span className="text-sm font-semibold">{t("common.history")}</span>
        </button>
      )}

      {hasNav && (
        <div className="flex min-w-[210px] flex-col gap-1.5">
          {prev && (
            <Link
              href={`/projects/${id}/${prev.slug}`}
              className="group flex items-center gap-2.5 rounded-lg bg-white/10 px-3.5 py-2.5 text-white ring-1 ring-white/15 transition hover:bg-white/20"
            >
              <ArrowLeft className="h-4 w-4 flex-shrink-0 text-white/70 transition group-hover:-translate-x-0.5" />
              <span className="flex min-w-0 flex-col items-start leading-none">
                <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-white/55">{t("common.prevStep")}</span>
                <span className="mt-0.5 truncate text-base font-bold">{t(prev.labelKey)}</span>
              </span>
            </Link>
          )}
          {options.map((opt) => (
            <Link
              key={opt.href}
              href={opt.href}
              onClick={opt.onClick}
              className={cn(
                "group flex items-center justify-between gap-2.5 rounded-lg px-3.5 py-2.5 text-white shadow-[0_10px_28px_-12px_rgba(15,118,110,0.55)] ring-1 transition",
                TONE[opt.tone ?? "cyan"],
              )}
            >
              <span className="flex min-w-0 flex-col items-start leading-none">
                {single && (
                  <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-white/80">{t("common.nextStep")}</span>
                )}
                <span className={cn("truncate font-extrabold", single ? "mt-0.5 text-base" : "text-base")}>{opt.label}</span>
              </span>
              <ArrowRight className="h-4 w-4 flex-shrink-0 transition group-hover:translate-x-0.5" />
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
