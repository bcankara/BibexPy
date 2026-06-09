"use client";

/**
 * Bibliometric tool ecosystem badges.
 *
 * İki katmanlı bir tasarım:
 *  - "Featured" araçlar: VOSviewer, bibliometrix → tam logo, pill border,
 *    hafif gölge.
 *  - Diğer araçlar: kompakt logo + isim chip.
 *
 * Logo dosyaları `apps/web/public/tools/` altında tutulur; yoksa text-only
 * fallback gösterilir.
 *
 * Mayıs 2026 itibariyle aktif olarak kullanılan araçlar baz alınmıştır.
 */

import { useState } from "react";
import { cn } from "@/lib/cn";

export type ToolKey =
  | "vosviewer"
  | "bibliometrix"
  | "biblioshiny"
  | "citespace"
  | "bibexcel"
  | "histcite"
  | "gephi"
  | "citnetexplorer"
  | "zotero"
  | "mendeley"
  | "jabref"
  | "endnote"
  | "citavi"
  | "refworks"
  | "papers"
  | "latex"
  | "overleaf"
  | "excel"
  | "tableau"
  | "powerbi"
  | "python"
  | "r"
  | "openrefine"
  | "scite";

type ToolMeta = {
  label: string;
  /** Renkli aksan — text + dot rengi */
  color: string;
  /** Açıklama (tooltip) */
  tip: string;
  /** Public/tools/ altındaki doğrudan logo yolu */
  logoSrc?: string;
  /** Logo görselinin kendisi brand adını içeriyorsa true → label tekrarlanmaz */
  logoHasName?: boolean;
};

export const TOOLS: Record<ToolKey, ToolMeta> = {
  // — Featured (logo'lu) — Logo görselleri zaten brand ismini içeriyor, label tekrarlanmasın
  vosviewer:    { label: "VOSviewer",    color: "text-amber-700",   logoSrc: "/tools/vosviewer.png",    logoHasName: true, tip: "VOSviewer — Network visualization (co-authorship, co-citation, keyword co-occurrence maps)" },
  bibliometrix: { label: "bibliometrix", color: "text-blue-800",    logoSrc: "/tools/bibliometrix.png", logoHasName: true, tip: "bibliometrix — R package for comprehensive science mapping (Aria & Cuccurullo)" },

  // — Bibliometric mapping & analysis —
  biblioshiny:    { label: "biblioshiny",    color: "text-sky-700",      logoSrc: "/tools/biblioshiny.svg",    tip: "Web UI for bibliometrix (no coding required)" },
  citespace:      { label: "CiteSpace",      color: "text-emerald-700",  logoSrc: "/tools/citespace.svg",      tip: "Citation network & research-front detection" },
  bibexcel:       { label: "BibExcel",       color: "text-green-700",    logoSrc: "/tools/bibexcel.svg",       tip: "Lightweight bibliometric data preprocessing" },
  histcite:       { label: "HistCite",       color: "text-purple-700",   logoSrc: "/tools/histcite.svg",       tip: "Historiographic citation analysis" },
  gephi:          { label: "Gephi",          color: "text-slate-700",    logoSrc: "/tools/gephi.svg",          tip: "General-purpose network visualization" },
  citnetexplorer: { label: "CitNetExplorer", color: "text-teal-700",     logoSrc: "/tools/citnetexplorer.svg", tip: "Direct citation network exploration" },

  // — Reference managers —
  zotero:    { label: "Zotero",    color: "text-red-700",     logoSrc: "/tools/zotero.svg",    tip: "Open-source reference manager" },
  mendeley:  { label: "Mendeley",  color: "text-rose-700",    logoSrc: "/tools/mendeley.svg",  tip: "Elsevier reference manager (free tier)" },
  jabref:    { label: "JabRef",    color: "text-orange-700",  logoSrc: "/tools/jabref.svg",    tip: "Java open-source BibTeX editor" },
  endnote:   { label: "EndNote",   color: "text-indigo-700",  logoSrc: "/tools/endnote.svg",   tip: "Clarivate reference manager" },
  citavi:    { label: "Citavi",    color: "text-fuchsia-700", logoSrc: "/tools/citavi.svg",    tip: "Reference + knowledge management (Lumivero)" },
  refworks:  { label: "RefWorks",  color: "text-violet-700",  logoSrc: "/tools/refworks.svg",  tip: "ProQuest cloud-based reference manager" },
  papers:    { label: "Papers",    color: "text-cyan-700",    logoSrc: "/tools/papers.svg",    tip: "ReadCube reference manager" },

  // — LaTeX ecosystem —
  latex:    { label: "LaTeX",    color: "text-stone-700",  logoSrc: "/tools/latex.svg",    tip: "Typesetting system — works with .bib" },
  overleaf: { label: "Overleaf", color: "text-green-700",  logoSrc: "/tools/overleaf.svg", tip: "Web-based LaTeX editor" },

  // — Spreadsheet / BI —
  excel:   { label: "Excel",     color: "text-emerald-700",  logoSrc: "/tools/excel.svg",   tip: "Microsoft spreadsheet — opens .xlsx natively" },
  tableau: { label: "Tableau",   color: "text-blue-700",     logoSrc: "/tools/tableau.svg", tip: "Salesforce BI / dashboards" },
  powerbi: { label: "Power BI",  color: "text-yellow-700",   logoSrc: "/tools/powerbi.svg", tip: "Microsoft BI / dashboards" },

  // — Data science —
  python:     { label: "Python",     color: "text-yellow-700",  logoSrc: "/tools/python.svg",     tip: "pandas / NetworkX / matplotlib" },
  r:          { label: "R",          color: "text-blue-700",    logoSrc: "/tools/r.svg",          tip: "Statistical computing — base of bibliometrix" },
  openrefine: { label: "OpenRefine", color: "text-lime-700",    logoSrc: "/tools/openrefine.svg", tip: "Data cleaning / transformation for messy CSV/TSV" },
  scite:      { label: "Scite",      color: "text-indigo-700",  logoSrc: "/tools/scite.svg",      tip: "Smart citations & assistant" },
};

/** ToolKey'ye karşılık gelen Tailwind dot color (text-X-700 → bg-X-500) */
const DOT_COLORS: Record<ToolKey, string> = {
  vosviewer:      "bg-amber-500",
  bibliometrix:   "bg-blue-700",
  biblioshiny:    "bg-sky-500",
  citespace:      "bg-emerald-500",
  bibexcel:       "bg-green-500",
  histcite:       "bg-purple-500",
  gephi:          "bg-slate-500",
  citnetexplorer: "bg-teal-500",
  zotero:         "bg-red-600",
  mendeley:       "bg-rose-500",
  jabref:         "bg-orange-500",
  endnote:        "bg-indigo-500",
  citavi:         "bg-fuchsia-500",
  refworks:       "bg-violet-500",
  papers:         "bg-cyan-500",
  latex:          "bg-stone-500",
  overleaf:       "bg-green-600",
  excel:          "bg-emerald-600",
  tableau:        "bg-blue-500",
  powerbi:        "bg-yellow-500",
  python:         "bg-yellow-600",
  r:              "bg-blue-600",
  openrefine:     "bg-lime-500",
  scite:          "bg-indigo-500",
};

export type ToolTier = "primary" | "secondary";

function ToolLogo({ tool, className }: { tool: ToolKey; className?: string }) {
  const meta = TOOLS[tool];
  const [failed, setFailed] = useState(false);
  if (!meta?.logoSrc || failed) {
    return (
      <span className={cn(
        "flex flex-shrink-0 items-center justify-center rounded text-[9px] font-bold text-white",
        DOT_COLORS[tool],
        className,
      )}>
        {meta.label.charAt(0).toUpperCase()}
      </span>
    );
  }
  return (
    <img
      src={meta.logoSrc}
      alt=""
      aria-hidden="true"
      onError={() => setFailed(true)}
      className={cn("flex-shrink-0 object-contain", className)}
    />
  );
}

/**
 * Logo'lu featured pill — VOSviewer / bibliometrix gibi araçlar için.
 * Logo görseli brand'in adını içeriyorsa (logoHasName) label tekrarlanmaz —
 * sadece logo gösterilir. Logo dosyası yoksa label otomatik fallback olur.
 *
 * Boyut: text chip'lerden belirgin şekilde büyük (~46-52px yükseklik) ki
 * "featured" hissi gerçekten verilsin.
 */
export function FeaturedTool({ tool }: { tool: ToolKey }) {
  const meta = TOOLS[tool];
  const [logoFailed, setLogoFailed] = useState(false);
  if (!meta) return null;
  const hasLogo = !!meta.logoSrc && !logoFailed;
  // Logo'da brand adı varsa ve logo görünüyorsa label gizle. Logo yoksa label fallback.
  const showLabel = !meta.logoHasName || !hasLogo;

  return (
    <span
      title={meta.tip}
      className={cn(
        "inline-flex items-center gap-2 rounded-lg border border-border bg-white px-2.5 py-1.5",
        "shadow-soft hover:border-brand-400 transition",
      )}
    >
      {hasLogo && (
        <img
          src={meta.logoSrc}
          onError={() => setLogoFailed(true)}
          alt={meta.label}
          className="h-6 max-w-[132px] object-contain flex-shrink-0"
        />
      )}
      {showLabel && (
        <span className={cn("text-sm font-bold whitespace-nowrap", meta.color)}>
          {meta.label}
        </span>
      )}
    </span>
  );
}

/** Kompakt chip — logo + isim, ince border. Logo yoksa monogram fallback. */
export function TextChip({ tool, tier = "primary" }: { tool: ToolKey; tier?: ToolTier }) {
  const meta = TOOLS[tool];
  if (!meta) return null;
  return (
    <span
      title={meta.tip}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border border-border/70 bg-white py-0.5 pl-0.5 pr-2",
        "hover:border-brand-300 transition",
        tier === "secondary" && "opacity-70",
      )}
    >
      <ToolLogo tool={tool} className="h-4 w-4 rounded-sm" />
      <span className={cn("text-[11px] font-medium whitespace-nowrap", meta.color)}>
        {meta.label}
      </span>
    </span>
  );
}

const FEATURED_TOOLS = new Set<ToolKey>(["vosviewer", "bibliometrix"]);

/**
 * İki gruba ayrılmış görüntü:
 *  - Üstte logo'lu öne çıkanlar (VOSviewer / bibliometrix varsa)
 *  - Altta sade text chip'ler
 *
 * `primary` listesi her zaman tam gösterilir.
 * `secondary` listesi `limit` parametresine kadar gösterilir, kalan +N.
 */
export function ToolBadgeList({ primary, secondary, limit }: {
  primary?: ToolKey[];
  secondary?: ToolKey[];
  limit?: number;
}) {
  const all = [
    ...(primary ?? []).map((t) => ({ t, tier: "primary" as const })),
    ...(secondary ?? []).map((t) => ({ t, tier: "secondary" as const })),
  ];
  const featured = all.filter((x) => FEATURED_TOOLS.has(x.t));
  const rest = all.filter((x) => !FEATURED_TOOLS.has(x.t));

  // Featured zaten featured'i çıkardığı için kalan dağılımı limit'le hesapla
  const shownRest = limit ? rest.slice(0, Math.max(0, limit - featured.length)) : rest;
  const overflow = rest.length - shownRest.length;

  return (
    <div className="space-y-3">
      {featured.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {featured.map(({ t }) => (
            <FeaturedTool key={t} tool={t} />
          ))}
        </div>
      )}
      {(shownRest.length > 0 || overflow > 0) && (
        <div className="flex flex-wrap gap-1.5">
          {shownRest.map(({ t, tier }) => (
            <TextChip key={t} tool={t} tier={tier} />
          ))}
          {overflow > 0 && (
            <span className="text-[10px] text-muted px-2 py-0.5 rounded-full border border-border/60 bg-bg-soft">
              +{overflow}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/** Geriye uyumluluk: Eski API. Yeni kodda ToolBadgeList veya doğrudan FeaturedTool/TextChip kullan. */
export function ToolBadge({ tool, tier = "primary" }: { tool: ToolKey; tier?: ToolTier; size?: "xs" | "sm" }) {
  if (FEATURED_TOOLS.has(tool)) return <FeaturedTool tool={tool} />;
  return <TextChip tool={tool} tier={tier} />;
}
