"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown, ChevronRight, Search, X, Plus, Sparkles,
  Calendar, Quote, FileType2, Languages, Database, Newspaper, UserSearch,
  Tag, Layers, Type, ShieldCheck, Filter as FilterIcon,
} from "lucide-react";
import type { FilterSpec, Facets, Range } from "@/lib/api-client";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/cn";

type Props = {
  spec: FilterSpec;
  facetsAll?: Facets;
  onChange: (next: FilterSpec) => void;
  onReset: () => void;
};

export function FilterPanel({ spec, facetsAll, onChange, onReset }: Props) {
  const t = useT();
  // Aktif filtre sayısı
  const activeCount = useMemo(() => countActive(spec), [spec]);

  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold flex items-center gap-1.5 text-sm">
          <FilterIcon className="h-4 w-4 text-brand-500" /> {t("records.openFilter")}
          {activeCount > 0 && (
            <span className="ml-1 px-1.5 py-0.5 rounded-full bg-brand-500 text-white text-[10px] font-bold">
              {activeCount}
            </span>
          )}
        </h2>
        {activeCount > 0 && (
          <button onClick={onReset} className="text-[11px] text-muted hover:text-danger flex items-center gap-1">
            <X className="h-3 w-3" /> {t("common.reset")}
          </button>
        )}
      </div>

      {/* Quick filters */}
      <QuickFilters spec={spec} facetsAll={facetsAll} onChange={onChange} />

      <Section title={t("records.filter.year")} icon={<Calendar className="h-3.5 w-3.5" />} active={!!spec.year} onClear={() => onChange({ ...spec, year: undefined })}>
        <RangeWithHistogram
          value={spec.year}
          min={facetsAll?.year?.min}
          max={facetsAll?.year?.max}
          histogram={facetsAll?.year?.histogram?.map((h) => ({ key: h.year, count: h.count })) ?? []}
          onChange={(v) => onChange({ ...spec, year: v })}
        />
      </Section>

      <Section title={t("records.filter.citationCount")} icon={<Quote className="h-3.5 w-3.5" />} active={!!spec.citation_count} onClear={() => onChange({ ...spec, citation_count: undefined })}>
        <RangeInput
          value={spec.citation_count}
          min={facetsAll?.citation_count?.min}
          max={facetsAll?.citation_count?.max}
          onChange={(v) => onChange({ ...spec, citation_count: v })}
          hint={facetsAll?.citation_count ? t("records.filter.citationHint", { mean: Math.round(facetsAll.citation_count.mean), max: facetsAll.citation_count.max }) : undefined}
        />
      </Section>

      <Section title={t("records.filter.docType")} icon={<FileType2 className="h-3.5 w-3.5" />} active={(spec.doc_type?.length ?? 0) > 0} count={spec.doc_type?.length} onClear={() => onChange({ ...spec, doc_type: undefined })}>
        <SearchableMulti
          placeholder={t("records.filter.docTypeSearch")}
          options={facetsAll?.doc_type ?? []}
          selected={spec.doc_type ?? []}
          onChange={(v) => onChange({ ...spec, doc_type: v.length ? v : undefined })}
        />
      </Section>

      <Section title={t("records.filter.language")} icon={<Languages className="h-3.5 w-3.5" />} active={(spec.language?.length ?? 0) > 0} count={spec.language?.length} onClear={() => onChange({ ...spec, language: undefined })}>
        <SearchableMulti
          placeholder={t("records.filter.languageSearch")}
          options={facetsAll?.language ?? []}
          selected={spec.language ?? []}
          onChange={(v) => onChange({ ...spec, language: v.length ? v : undefined })}
        />
      </Section>

      <Section title={t("records.filter.dbSource")} icon={<Database className="h-3.5 w-3.5" />} active={(spec.db_source?.length ?? 0) > 0} count={spec.db_source?.length} onClear={() => onChange({ ...spec, db_source: undefined })}>
        <SearchableMulti
          placeholder={t("records.filter.dbSourceSearch")}
          options={facetsAll?.db_source ?? []}
          selected={spec.db_source ?? []}
          onChange={(v) => onChange({ ...spec, db_source: v.length ? v : undefined })}
        />
      </Section>

      <Section title={t("records.filter.journal")} icon={<Newspaper className="h-3.5 w-3.5" />} active={(spec.journal?.length ?? 0) > 0} count={spec.journal?.length} onClear={() => onChange({ ...spec, journal: undefined })}>
        <SuggestInput
          values={spec.journal ?? []}
          options={facetsAll?.journal_top?.map((j) => j.value) ?? []}
          counts={Object.fromEntries(facetsAll?.journal_top?.map((j) => [j.value, j.count]) ?? [])}
          onChange={(v) => onChange({ ...spec, journal: v.length ? v : undefined })}
          placeholder={t("records.filter.journalPlaceholder")}
          subtitle={t("records.filter.journalSubtitle")}
        />
      </Section>

      <Section title={t("records.filter.authors")} icon={<UserSearch className="h-3.5 w-3.5" />} active={(spec.authors?.length ?? 0) > 0} count={spec.authors?.length} onClear={() => onChange({ ...spec, authors: undefined })}>
        <SuggestInput
          values={spec.authors ?? []}
          options={[]}
          counts={{}}
          onChange={(v) => onChange({ ...spec, authors: v.length ? v : undefined })}
          placeholder={t("records.filter.authorsPlaceholder")}
          subtitle={t("records.filter.authorsSubtitle")}
        />
      </Section>

      <Section title={t("records.filter.wcCategories")} icon={<Tag className="h-3.5 w-3.5" />} active={(spec.wc_categories?.length ?? 0) > 0} count={spec.wc_categories?.length} onClear={() => onChange({ ...spec, wc_categories: undefined })}>
        <SuggestInput
          values={spec.wc_categories ?? []}
          options={[]}
          counts={{}}
          onChange={(v) => onChange({ ...spec, wc_categories: v.length ? v : undefined })}
          placeholder={t("records.filter.wcPlaceholder")}
        />
      </Section>

      <Section title={t("records.filter.scCategories")} icon={<Layers className="h-3.5 w-3.5" />} active={(spec.sc_categories?.length ?? 0) > 0} count={spec.sc_categories?.length} onClear={() => onChange({ ...spec, sc_categories: undefined })}>
        <SuggestInput
          values={spec.sc_categories ?? []}
          options={[]}
          counts={{}}
          onChange={(v) => onChange({ ...spec, sc_categories: v.length ? v : undefined })}
          placeholder={t("records.filter.scPlaceholder")}
        />
      </Section>

      <Section title={t("records.filter.fulltext")} icon={<Type className="h-3.5 w-3.5" />} active={!!spec.fulltext?.query} highlight onClear={() => onChange({ ...spec, fulltext: undefined })}>
        <FulltextInput
          value={spec.fulltext}
          onChange={(v) => onChange({ ...spec, fulltext: v })}
        />
      </Section>

      <Section title={t("records.filter.quality")} icon={<ShieldCheck className="h-3.5 w-3.5" />} active={!!spec.quality && ((spec.quality.missing?.length ?? 0) + (spec.quality.has?.length ?? 0)) > 0} onClear={() => onChange({ ...spec, quality: undefined })}>
        <QualityInput
          value={spec.quality}
          onChange={(q) => onChange({ ...spec, quality: q })}
        />
      </Section>
    </div>
  );
}

/* ───────────────────────── Quick Filters ───────────────────────── */

function QuickFilters({ spec, facetsAll, onChange }: {
  spec: FilterSpec; facetsAll?: Facets; onChange: (n: FilterSpec) => void;
}) {
  const t = useT();
  const maxYear = facetsAll?.year?.max;
  const chips: { label: string; active: boolean; apply: () => void }[] = [
    {
      label: t("records.filter.last5Years"),
      active: !!(spec.year?.min && maxYear && spec.year.min === maxYear - 4),
      apply: () => onChange({ ...spec, year: maxYear ? { min: maxYear - 4, max: maxYear } : spec.year }),
    },
    {
      label: t("records.filter.last10Years"),
      active: !!(spec.year?.min && maxYear && spec.year.min === maxYear - 9),
      apply: () => onChange({ ...spec, year: maxYear ? { min: maxYear - 9, max: maxYear } : spec.year }),
    },
    {
      label: t("records.filter.hasDoi"),
      active: !!spec.quality?.has?.includes("DI"),
      apply: () => {
        const has = new Set(spec.quality?.has ?? []);
        if (has.has("DI")) has.delete("DI"); else has.add("DI");
        const next = [...has];
        onChange({ ...spec, quality: { ...spec.quality, has: next.length ? next : undefined } });
      },
    },
    {
      label: t("records.filter.hasAbstract"),
      active: !!spec.quality?.has?.includes("AB"),
      apply: () => {
        const has = new Set(spec.quality?.has ?? []);
        if (has.has("AB")) has.delete("AB"); else has.add("AB");
        const next = [...has];
        onChange({ ...spec, quality: { ...spec.quality, has: next.length ? next : undefined } });
      },
    },
    {
      label: t("records.filter.highCitation"),
      active: spec.citation_count?.min === 10,
      apply: () => onChange({ ...spec, citation_count: { min: 10 } }),
    },
  ];

  return (
    <div className="rounded-lg border border-brand-200/60 bg-brand-50/40 px-2 py-2">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-brand-700 mb-1.5 flex items-center gap-1">
        <Sparkles className="h-3 w-3" /> {t("records.filter.quickFilters")}
      </p>
      <div className="flex flex-wrap gap-1">
        {chips.map((c) => (
          <button
            key={c.label}
            onClick={c.apply}
            className={cn(
              "text-[11px] px-2 py-0.5 rounded-full border transition",
              c.active
                ? "bg-brand-500 text-white border-brand-500"
                : "bg-white border-border text-ink hover:border-brand-400",
            )}
          >
            {c.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/* ───────────────────────── Section wrapper ───────────────────────── */

function Section({
  title, icon, children, active, count, highlight, onClear,
}: {
  title: string; icon?: React.ReactNode; children: React.ReactNode;
  active?: boolean; count?: number; highlight?: boolean;
  /** #1: bu filtreyi tek başına temizle (global reset'ten bağımsız). */
  onClear?: () => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(!!active || highlight);
  useEffect(() => { if (active) setOpen(true); }, [active]);

  return (
    <div className={cn(
      "border rounded-md bg-white transition",
      active ? "border-brand-500/60 shadow-soft" : "border-border",
    )}>
      <div className="w-full flex items-center gap-2 px-3 py-2 text-[13px] font-medium rounded-md hover:bg-bg-soft">
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-2 flex-1 min-w-0 text-left"
        >
          {open ? <ChevronDown className="h-3 w-3 text-muted flex-shrink-0" /> : <ChevronRight className="h-3 w-3 text-muted flex-shrink-0" />}
          {icon && <span className={cn("flex-shrink-0", active ? "text-brand-500" : "text-muted")}>{icon}</span>}
          <span className="flex-1 text-left truncate">{title}</span>
        </button>
        {count != null && count > 0 && (
          <span className="flex-shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-brand-100 text-brand-700">{count}</span>
        )}
        {active && count == null && (
          <span className="flex-shrink-0 w-1.5 h-1.5 rounded-full bg-brand-500" />
        )}
        {/* #1: per-filtre temizle — yalnız bu filtre aktifken */}
        {active && onClear && (
          <button
            onClick={(e) => { e.stopPropagation(); onClear(); }}
            title={t("records.filter.clearThis")}
            className="flex-shrink-0 text-muted hover:text-danger p-0.5 -mr-0.5"
          >
            <X className="h-3 w-3" />
          </button>
        )}
      </div>
      {open && <div className="px-3 pb-3 pt-1">{children}</div>}
    </div>
  );
}

/* ───────────────────────── Range with histogram ───────────────────────── */

function RangeWithHistogram({
  value, min, max, histogram, onChange,
}: {
  value: Range | undefined; min?: number; max?: number;
  histogram: { key: number; count: number }[];
  onChange: (v: Range | undefined) => void;
}) {
  const v = value ?? {};
  const actualMin = min ?? 1900;
  const actualMax = max ?? new Date().getFullYear();
  const lo = v.min ?? actualMin;
  const hi = v.max ?? actualMax;

  const maxCount = Math.max(1, ...histogram.map((h) => h.count));
  const span = Math.max(1, actualMax - actualMin);

  function commit(nextLo: number, nextHi: number) {
    const cLo = Math.max(actualMin, Math.min(nextLo, nextHi));
    const cHi = Math.min(actualMax, Math.max(nextHi, nextLo));
    const isFull = cLo === actualMin && cHi === actualMax;
    onChange(isFull ? undefined : { min: cLo, max: cHi });
  }

  const t = useT();
  // Slider için yüzde
  const loPct = ((lo - actualMin) / span) * 100;
  const hiPct = ((hi - actualMin) / span) * 100;

  return (
    <div className="space-y-2.5">
      {/* Histogram */}
      {histogram.length > 0 && (
        <div className="flex items-end gap-px h-14 px-0.5">
          {histogram.map((h) => {
            const inRange = h.key >= lo && h.key <= hi;
            return (
              <div
                key={h.key}
                className="flex-1 h-full flex items-end group relative cursor-help"
                aria-label={t("records.filter.yearCount", { year: h.key, count: h.count })}
              >
                <div
                  className={cn(
                    "w-full rounded-t-sm transition-colors group-hover:brightness-110",
                    inRange ? "bg-gradient-to-t from-brand-500 to-brand-400" : "bg-border",
                  )}
                  style={{ height: `${(h.count / maxCount) * 100}%`, minHeight: 2 }}
                />
                <div className="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-1 z-20 hidden whitespace-nowrap rounded bg-ink px-1.5 py-0.5 text-[10px] font-medium text-white shadow-soft group-hover:block">
                  {t("records.filter.yearCount", { year: h.key, count: h.count })}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Dual range slider — overlapping native inputs */}
      <div className="relative h-7 px-1">
        {/* Track */}
        <div className="absolute left-1 right-1 top-1/2 -translate-y-1/2 h-1 rounded-full bg-bg-soft">
          <div
            className="absolute top-0 bottom-0 bg-brand-500 rounded-full"
            style={{ left: `${loPct}%`, right: `${100 - hiPct}%` }}
          />
        </div>
        {/* MIN thumb */}
        <input
          type="range"
          min={actualMin} max={actualMax} step={1}
          value={lo}
          onChange={(e) => commit(Number(e.target.value), hi)}
          className="dual-range absolute inset-0 w-full appearance-none bg-transparent pointer-events-none"
          style={{ zIndex: lo > actualMax - 5 ? 5 : 3 }}
        />
        {/* MAX thumb */}
        <input
          type="range"
          min={actualMin} max={actualMax} step={1}
          value={hi}
          onChange={(e) => commit(lo, Number(e.target.value))}
          className="dual-range absolute inset-0 w-full appearance-none bg-transparent pointer-events-none"
          style={{ zIndex: 4 }}
        />
      </div>

      {/* Sayısal değerler + manual input */}
      <div className="flex items-center gap-2">
        <input
          type="number"
          min={actualMin} max={actualMax}
          value={lo}
          onChange={(e) => {
            const n = e.target.value === "" ? actualMin : Number(e.target.value);
            commit(n, hi);
          }}
          className="w-full rounded-md border border-border bg-white px-2 py-1 text-xs tabular-nums focus:outline-none focus:border-brand-500"
        />
        <span className="text-muted text-xs">—</span>
        <input
          type="number"
          min={actualMin} max={actualMax}
          value={hi}
          onChange={(e) => {
            const n = e.target.value === "" ? actualMax : Number(e.target.value);
            commit(lo, n);
          }}
          className="w-full rounded-md border border-border bg-white px-2 py-1 text-xs tabular-nums focus:outline-none focus:border-brand-500"
        />
      </div>

      {/* Range info */}
      <div className="flex items-center justify-between text-[10px] text-muted">
        <span>{t("records.filter.range")}: {actualMin}–{actualMax}</span>
        <span className="tabular-nums">{t("records.filter.yearsSelected", { n: hi - lo + 1 })}</span>
      </div>
    </div>
  );
}

function RangeInput({ value, min, max, onChange, hint }: {
  value: Range | undefined; min?: number; max?: number;
  onChange: (v: Range | undefined) => void;
  hint?: string;
}) {
  const v = value ?? {};
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <input
          type="number" placeholder={String(min ?? "min")} value={v.min ?? ""}
          onChange={(e) => {
            const n = e.target.value === "" ? undefined : Number(e.target.value);
            const out = { ...v, min: n };
            onChange(out.min == null && out.max == null ? undefined : out);
          }}
          className="w-full rounded-md border border-border bg-white px-2 py-1.5 text-xs tabular-nums focus:outline-none focus:border-brand-500"
        />
        <span className="text-muted text-xs">—</span>
        <input
          type="number" placeholder={String(max ?? "max")} value={v.max ?? ""}
          onChange={(e) => {
            const n = e.target.value === "" ? undefined : Number(e.target.value);
            const out = { ...v, max: n };
            onChange(out.min == null && out.max == null ? undefined : out);
          }}
          className="w-full rounded-md border border-border bg-white px-2 py-1.5 text-xs tabular-nums focus:outline-none focus:border-brand-500"
        />
      </div>
      {hint && <p className="text-[10px] text-muted">{hint}</p>}
    </div>
  );
}

/* ───────────────────────── Searchable Multi-Select (cmdk) ───────────────────────── */

function SearchableMulti({
  placeholder, options, selected, onChange,
}: {
  placeholder: string;
  options: { value: string; count: number }[];
  selected: string[];
  onChange: (v: string[]) => void;
}) {
  const t = useT();
  if (options.length === 0) return <p className="text-xs text-muted py-1">{t("common.noData")}</p>;
  const set = new Set(selected);
  const [search, setSearch] = useState("");

  function toggle(val: string) {
    const next = new Set(selected);
    if (next.has(val)) next.delete(val); else next.add(val);
    onChange([...next]);
  }

  const showSearch = options.length > 6;
  const filtered = search
    ? options.filter((o) => o.value.toLowerCase().includes(search.toLowerCase()))
    : options;

  return (
    <div className="space-y-1.5">
      {showSearch && (
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={placeholder}
            className="w-full rounded-md border border-border bg-white pl-7 pr-2 py-1.5 text-xs focus:outline-none focus:border-brand-500"
          />
        </div>
      )}
      {selected.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {selected.map((v) => (
            <button
              key={v}
              onClick={() => toggle(v)}
              className="text-[11px] px-1.5 py-0.5 rounded bg-brand-500 text-white flex items-center gap-1 hover:bg-brand-600"
            >
              {v} <X className="h-2.5 w-2.5" />
            </button>
          ))}
        </div>
      )}
      <div className="max-h-44 overflow-y-auto space-y-0.5 -mx-1 px-1">
        {filtered.length === 0 ? (
          <p className="text-[11px] text-muted py-2 text-center">{t("records.filter.noMatch")}</p>
        ) : filtered.slice(0, 200).map((o) => {
          const sel = set.has(o.value);
          return (
            <label
              key={o.value}
              className={cn(
                "flex items-center gap-2 text-xs cursor-pointer rounded px-1.5 py-1 hover:bg-bg-soft",
                sel && "bg-brand-50",
              )}
            >
              <input
                type="checkbox"
                checked={sel}
                onChange={() => toggle(o.value)}
                className="accent-brand-500 h-3.5 w-3.5"
              />
              <span className="flex-1 truncate" title={o.value}>{o.value}</span>
              <span className="text-[10px] text-muted tabular-nums">{o.count}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
}

/* ───────────────────────── Suggest input (chips + autocomplete) ───────────────────────── */

function SuggestInput({
  values, options, counts, onChange, placeholder, subtitle,
}: {
  values: string[];
  options: string[];
  counts: Record<string, number>;
  onChange: (v: string[]) => void;
  placeholder?: string;
  subtitle?: string;
}) {
  const t = useT();
  const [text, setText] = useState("");
  const [showSuggest, setShowSuggest] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setShowSuggest(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  function add(v: string) {
    const t = v.trim();
    if (!t) return;
    if (values.includes(t)) return;
    onChange([...values, t]);
    setText("");
    setShowSuggest(false);
  }

  const filtered = text
    ? options.filter((o) => o.toLowerCase().includes(text.toLowerCase()) && !values.includes(o)).slice(0, 8)
    : options.filter((o) => !values.includes(o)).slice(0, 8);

  return (
    <div className="space-y-1.5" ref={ref}>
      <div className="relative">
        <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted" />
        <input
          value={text}
          onChange={(e) => { setText(e.target.value); setShowSuggest(true); }}
          onFocus={() => setShowSuggest(true)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); add(text); }
            else if (e.key === "Escape") setShowSuggest(false);
          }}
          placeholder={placeholder}
          className="w-full rounded-md border border-border bg-white pl-7 pr-7 py-1.5 text-xs focus:outline-none focus:border-brand-500"
        />
        {text && (
          <button
            onClick={() => add(text)}
            className="absolute right-1 top-1/2 -translate-y-1/2 p-1 rounded hover:bg-brand-50 text-brand-600"
            title={t("records.filter.add")}
          >
            <Plus className="h-3 w-3" />
          </button>
        )}

        {showSuggest && filtered.length > 0 && (
          <div className="absolute left-0 right-0 top-full mt-1 z-30 rounded-md border border-border bg-white shadow-soft max-h-52 overflow-y-auto">
            {filtered.map((o) => (
              <button
                key={o}
                onClick={() => add(o)}
                className="w-full text-left px-2 py-1 text-xs hover:bg-brand-50 flex items-center gap-2"
              >
                <span className="flex-1 truncate" title={o}>{o}</span>
                {counts[o] != null && (
                  <span className="text-[10px] text-muted tabular-nums">{counts[o]}</span>
                )}
              </button>
            ))}
          </div>
        )}
      </div>
      {subtitle && <p className="text-[10px] text-muted">{subtitle}</p>}
      {values.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {values.map((v, i) => (
            <button
              key={i}
              onClick={() => onChange(values.filter((_, idx) => idx !== i))}
              className="text-[11px] px-1.5 py-0.5 rounded bg-brand-500 text-white flex items-center gap-1 hover:bg-brand-600"
            >
              {v} <X className="h-2.5 w-2.5" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ───────────────────────── Fulltext input ───────────────────────── */

function FulltextInput({ value, onChange }: {
  value: { query: string; fields?: string[] } | undefined;
  onChange: (v: { query: string; fields?: string[] } | undefined) => void;
}) {
  const t = useT();
  const ALL_FIELDS = [
    { key: "TI", label: t("recordDetail.fields.TI") },
    { key: "AB", label: t("recordDetail.fields.AB") },
    { key: "DE", label: t("recordDetail.fields.DE") },
    { key: "ID", label: t("recordDetail.fields.ID") },
  ];
  const fields = value?.fields ?? ["TI", "AB", "DE", "ID"];
  const fieldSet = new Set(fields);

  return (
    <div className="space-y-1.5">
      <input
        type="text"
        value={value?.query ?? ""}
        onChange={(e) => {
          const q = e.target.value;
          onChange(q.trim() ? { query: q, fields } : undefined);
        }}
        placeholder={t("records.filter.fulltextPlaceholder")}
        className="w-full rounded-md border border-border bg-white px-2 py-1.5 text-sm font-mono focus:outline-none focus:border-brand-500"
      />
      <p className="text-[10px] text-muted">{t("records.filter.fulltextHint")}</p>
      <div className="flex flex-wrap gap-1 pt-1">
        {ALL_FIELDS.map((f) => {
          const on = fieldSet.has(f.key);
          return (
            <button
              key={f.key}
              onClick={() => {
                const next = new Set(fields);
                if (next.has(f.key)) next.delete(f.key); else next.add(f.key);
                const arr = [...next];
                onChange(value?.query ? { query: value.query, fields: arr.length ? arr : undefined } : undefined);
              }}
              className={cn(
                "text-[10px] px-1.5 py-0.5 rounded border",
                on ? "bg-brand-500 text-white border-brand-500" : "bg-white border-border text-muted",
              )}
              title={f.label}
            >
              {f.key}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ───────────────────────── Quality input ───────────────────────── */

function QualityInput({ value, onChange }: {
  value: { missing?: string[]; has?: string[] } | undefined;
  onChange: (q: { missing?: string[]; has?: string[] } | undefined) => void;
}) {
  const t = useT();
  const fields = [
    { k: "DI", l: "DOI" },
    { k: "AB", l: t("recordDetail.fields.AB") },
    { k: "DE", l: t("recordDetail.fields.DE") },
    { k: "ID", l: t("recordDetail.fields.ID") },
    { k: "WC", l: t("recordDetail.fields.WC") },
    { k: "SC", l: t("recordDetail.fields.SC") },
    { k: "TC", l: t("recordDetail.fields.TC") },
  ];
  const missing = new Set(value?.missing ?? []);
  const has = new Set(value?.has ?? []);

  function update(missing: Set<string>, has: Set<string>) {
    const m = [...missing], h = [...has];
    const empty = m.length === 0 && h.length === 0;
    onChange(empty ? undefined : { missing: m.length ? m : undefined, has: h.length ? h : undefined });
  }

  return (
    <div className="space-y-2">
      <div>
        <p className="text-[10px] text-muted mb-1">{t("records.filter.qualityHasLabel")}:</p>
        <div className="flex flex-wrap gap-1">
          {fields.map((f) => {
            const on = has.has(f.k);
            return (
              <button
                key={"h" + f.k}
                onClick={() => {
                  const n = new Set(has);
                  if (n.has(f.k)) n.delete(f.k); else { n.add(f.k); missing.delete(f.k); }
                  update(missing, n);
                }}
                className={cn(
                  "text-[10px] px-1.5 py-0.5 rounded border tabular-nums",
                  on ? "bg-success text-white border-success" : "bg-white border-border text-muted hover:border-success",
                )}
                title={f.l}
              >
                {f.k}
              </button>
            );
          })}
        </div>
      </div>
      <div>
        <p className="text-[10px] text-muted mb-1">{t("records.filter.qualityMissingLabel")}:</p>
        <div className="flex flex-wrap gap-1">
          {fields.map((f) => {
            const on = missing.has(f.k);
            return (
              <button
                key={"m" + f.k}
                onClick={() => {
                  const n = new Set(missing);
                  if (n.has(f.k)) n.delete(f.k); else { n.add(f.k); has.delete(f.k); }
                  update(n, has);
                }}
                className={cn(
                  "text-[10px] px-1.5 py-0.5 rounded border tabular-nums",
                  on ? "bg-danger text-white border-danger" : "bg-white border-border text-muted hover:border-danger",
                )}
                title={f.l}
              >
                {f.k}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ───────────────────────── Helpers ───────────────────────── */

function countActive(spec: FilterSpec): number {
  let n = 0;
  if (spec.year && (spec.year.min != null || spec.year.max != null)) n++;
  if (spec.citation_count && (spec.citation_count.min != null || spec.citation_count.max != null)) n++;
  if (spec.doc_type?.length) n++;
  if (spec.language?.length) n++;
  if (spec.db_source?.length) n++;
  if (spec.journal?.length) n++;
  if (spec.authors?.length) n++;
  if (spec.wc_categories?.length) n++;
  if (spec.sc_categories?.length) n++;
  if (spec.fulltext?.query) n++;
  if (spec.quality && ((spec.quality.missing?.length ?? 0) + (spec.quality.has?.length ?? 0)) > 0) n++;
  return n;
}
