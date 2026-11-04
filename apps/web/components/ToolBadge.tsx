"use client";

/**
 * Bibliometric tool ecosystem badges.
 *
 * A two-tier design:
 *  - "Featured" tools: VOSviewer, bibliometrix → full logo, pill border,
 *    light shadow.
 *  - Other tools: compact logo + name chip.
 *
 * Logo files are kept under `apps/web/public/tools/`; if missing, a
 * text-only fallback is shown.
 *
 * Based on the tools in active use as of May 2026.
 */

import { useState } from "react";
import { cn } from "@/lib/cn";
import { useT } from "@/lib/i18n";

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
  /** Colored accent — text + dot color */
  color: string;
  /** English description (tooltip fallback; the translated text is tools.tips.<key>) */
  tip: string;
  /** Direct logo path under public/tools/ */
  logoSrc?: string;
  /** True if the logo image itself contains the brand name → label is not repeated */
  logoHasName?: boolean;
};

export const TOOLS: Record<ToolKey, ToolMeta> = {
  // — Featured (with logo) — The logo images already contain the brand name, so the label is not repeated
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

/** Tailwind dot color corresponding to a ToolKey (text-X-700 → bg-X-500) */
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
 * Featured pill with a logo — for tools such as VOSviewer / bibliometrix.
 * If the logo image contains the brand's name (logoHasName), the label is not
 * repeated — only the logo is shown. If the logo file is missing, the label
 * automatically becomes the fallback.
 *
 * Size: noticeably larger than the text chips (~46-52px tall) so that the
 * "featured" feel is actually conveyed.
 */
/** Tooltip text in the app language (tools.tips.<tool>), falling back to the English tip. */
function toolTip(t: (k: string) => string, tool: ToolKey, fallback: string): string {
  const key = `tools.tips.${tool}`;
  const v = t(key);
  return v === key ? fallback : v;
}

export function FeaturedTool({ tool }: { tool: ToolKey }) {
  const t = useT();
  const meta = TOOLS[tool];
  const [logoFailed, setLogoFailed] = useState(false);
  if (!meta) return null;
  const hasLogo = !!meta.logoSrc && !logoFailed;
  // Hide the label if the logo contains the brand name and the logo is visible. If there is no logo, the label is the fallback.
  const showLabel = !meta.logoHasName || !hasLogo;

  return (
    <span
      title={toolTip(t, tool, meta.tip)}
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

/** Compact chip — logo + name, thin border. If there is no logo, falls back to a monogram. */
export function TextChip({ tool, tier = "primary" }: { tool: ToolKey; tier?: ToolTier }) {
  const t = useT();
  const meta = TOOLS[tool];
  if (!meta) return null;
  return (
    <span
      title={toolTip(t, tool, meta.tip)}
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
 * A display split into two groups:
 *  - On top, the featured ones with logos (if VOSviewer / bibliometrix are present)
 *  - Below, plain text chips
 *
 * The `primary` list is always shown in full.
 * The `secondary` list is shown up to the `limit` parameter; the remainder is shown as +N.
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

  // Since the featured ones are already taken out, compute the remaining distribution against the limit
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

/** Backward compatibility: legacy API. In new code, use ToolBadgeList or FeaturedTool/TextChip directly. */
export function ToolBadge({ tool, tier = "primary" }: { tool: ToolKey; tier?: ToolTier; size?: "xs" | "sm" }) {
  if (FEATURED_TOOLS.has(tool)) return <FeaturedTool tool={tool} />;
  return <TextChip tool={tool} tier={tier} />;
}
