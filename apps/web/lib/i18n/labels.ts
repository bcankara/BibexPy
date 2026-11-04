import type { Locale } from "./index";
import { formatDateTime } from "./format";

/**
 * Shared "machine id -> translated label" helpers. The backend persists stable ids
 * (stage codes, chosen-source ids, audit kinds, user actions) and sometimes the English
 * display string of an id (older audit entries). Every helper accepts both forms and falls
 * back to the raw value, so unknown ids never render as an empty string.
 */

type TFn = (key: string, vars?: Record<string, string | number>) => string;

/** Translation lookup that reports a missing key as null instead of echoing the key. */
function lookup(t: TFn, key: string): string | null {
  const v = t(key);
  return v === key ? null : v;
}

/** "enrich_field" -> "Enrich field" (last-resort display of an unknown snake_case id). */
function humanize(code: string): string {
  const s = code.replace(/_/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : code;
}

// ---------------------------------------------------------------- match stages

/** Stage code or English stage label -> merge.stages.<key>. */
const STAGE_KEYS: Record<string, string> = {
  "1_doi_exact": "doiExact",
  "DOI exact": "doiExact",
  "2_pmid_exact": "pmidExact",
  "PMID exact": "pmidExact",
  "3_title_year_surname": "titleYearSurname",
  "Title+Year+Surname": "titleYearSurname",
  "4_journal_vol_page": "journalVolPage",
  "Journal+Vol+Pages": "journalVolPage",
  "5_borderline": "borderline",
  "Borderline (manual review)": "borderline",
};

export function stageLabel(t: TFn, raw: string): string {
  const k = STAGE_KEYS[raw];
  return (k && lookup(t, `merge.stages.${k}`)) || raw;
}

// ------------------------------------------------------------- field sources

/** wos | scopus | union | empty | cross_fill | scopus_fallback | wos_fallback. */
export function fieldSourceLabel(t: TFn, code: string): string {
  return lookup(t, `audit.smart.sources.${code}`) ?? code;
}

// --------------------------------------------------------------- audit kinds

/** Audit kinds whose dictionary key differs from the kind id. */
const KIND_ALIASES: Record<string, string> = {
  enrich_selected_requested: "enrich_selected",
};

export function auditKindLabel(t: TFn, kind: string): string {
  const k = KIND_ALIASES[kind] ?? kind;
  return lookup(t, `audit.kinds.${k}`) ?? humanize(kind);
}

// ------------------------------------------------------------- user actions

export function userActionLabel(t: TFn, code: string): string {
  return lookup(t, `audit.userActions.${code}`) ?? humanize(code);
}

// ---------------------------------------------------------- analysis labels

/** The backend names analyses "<Method> — dd.mm.yyyy HH:MM" (or an ISO-ish date) at creation. */
const AUTO_LABEL_RES = [
  /^(Smart|Single|Simple|Enhanced|Legacy) — \d\d\.\d\d\.\d{4} \d\d:\d\d$/,
  /^(Smart|Single|Simple|Enhanced|Legacy) — \d{4}-\d\d-\d\d \d\d:\d\d$/,
];

export function isAutoAnalysisLabel(label: string | null | undefined): boolean {
  return !!label && AUTO_LABEL_RES.some((re) => re.test(label));
}

function toDate(v: number | string): Date {
  // Backend timestamps are epoch seconds; strings are ISO dates.
  return typeof v === "number" ? new Date(v * 1000) : new Date(v);
}

/**
 * Title of an analysis in the app language. Auto-generated labels are rebuilt from method +
 * creation time (so they follow the UI language and date format); user-chosen labels and
 * legacy folder names are shown as stored.
 */
export function analysisDisplayLabel(
  t: TFn,
  locale: Locale,
  a: {
    label?: string | null;
    label_auto?: boolean | null;
    method?: string | null;
    created_at?: number | string | null;
  },
): string {
  const label = a.label ?? "";
  const auto = a.label_auto === true || isAutoAnalysisLabel(label);
  if (auto && a.created_at != null && a.created_at !== "") {
    const d = toDate(a.created_at);
    if (!Number.isNaN(d.getTime())) {
      const methodName =
        lookup(t, `analyses.method.${a.method ?? "unknown"}`) ?? lookup(t, "analyses.method.unknown") ?? "";
      return `${methodName} — ${formatDateTime(d, locale)}`;
    }
  }
  return label;
}

// ----------------------------------------------------------- filter criteria

/** Filter-spec key -> records.filter.<label key> (the labels the filter panel already uses). */
const FILTER_KEYS: Record<string, string> = {
  year: "year",
  citation_count: "citationCount",
  doc_type: "docType",
  language: "language",
  db_source: "dbSource",
  journal: "journal",
  authors: "authors",
  wc_categories: "wcCategories",
  sc_categories: "scCategories",
  fulltext: "fulltext",
  quality: "quality",
};

export function filterCriterionLabel(t: TFn, key: string): string {
  const k = FILTER_KEYS[key];
  return (k && lookup(t, `records.filter.${k}`)) || key;
}
