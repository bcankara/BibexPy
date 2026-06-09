import { cn } from "@/lib/cn";
import { ProjectNav } from "./ProjectNav";

type Props = {
  title: string;
  subtitle?: string;
  badges?: { label: string; tone?: "neutral" | "live" | "info" }[];
  right?: React.ReactNode;
  className?: string;
};

// Badge tonları — koyu navy banner (PageHeader brand-gradient) üzerinde:
//   neutral = beyaz şeffaf (genel)
//   live    = emerald (canlı/aktif durum, semantic)
//   info    = cyan (bilgi — V2 marka rengi ile uyumlu)
const BADGE_TONES: Record<string, string> = {
  neutral: "bg-white/15 text-white border-white/20",
  live: "bg-emerald-400/20 text-emerald-50 border-emerald-300/40",
  info: "bg-cyan-400/20 text-cyan-50 border-cyan-300/40",
};

export function PageHeader({ title, subtitle, badges, right, className }: Props) {
  return (
    <>
      {/* İç sayfaların üst bandı — düz dark zemin (#081c32). Hemen altında
          pipeline (ProjectNav) gelir; ProjectNav proje-dışı sayfalarda null döner. */}
      <section className={cn("bg-[#081c32] text-white", className)}>
      <div className="max-w-6xl mx-auto px-6 py-8">
        {badges && badges.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 mb-3">
            {badges.map((b, i) => (
              <span
                key={i}
                className={cn(
                  "inline-flex items-center px-2.5 py-1 rounded-md border text-[11px] font-semibold uppercase tracking-wide",
                  BADGE_TONES[b.tone ?? "neutral"]
                )}
              >
                {b.label}
              </span>
            ))}
          </div>
        )}
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-3xl font-bold leading-tight">{title}</h1>
            {subtitle && (
              <p className="text-white/80 mt-1.5 text-sm max-w-2xl">{subtitle}</p>
            )}
          </div>
          {right && <div className="text-white/90 text-sm">{right}</div>}
        </div>
      </div>
      </section>
      <ProjectNav />
    </>
  );
}
