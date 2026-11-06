/**
 * API base URL — RUNTIME origin detection (NOT dependent on a build-time env).
 *
 * Why runtime: the static export is produced from a single build but runs in two
 * different scenarios — (1) `npm run dev` with a separate backend (port 8001), (2) the
 * `bibexpy` package on the SAME origin as the backend (any port). Using a build-time
 * NEXT_PUBLIC_API_BASE caused the dev address in .env.local (localhost:8001) to be
 * baked into the package and lead to a "Failed to fetch" error.
 *
 * Rule:
 *   • Next dev server port (3000–3009) → separate backend http://<host>:8001/api
 *   • Every other case (package mode, any port) → same origin "/api"
 *   • SSR/prerender (no window) → "/api" (fetches are client-side anyway)
 *
 * The backend mounts all routers under `/api/...`.
 */
function resolveApiBase(): string {
  if (typeof window !== "undefined") {
    const { protocol, hostname, port } = window.location;
    if (/^300\d$/.test(port)) {
      return `${protocol}//${hostname}:8001/api`;
    }
    return "/api";
  }
  return "/api";
}

export const BASE = resolveApiBase();

/**
 * API origin (BASE with `/api` stripped) and the Swagger docs URL — same
 * runtime logic. Relative in package mode ("" / "/docs"), absolute in dev
 * (http://host:8001/docs). The AppShell "API" link uses this.
 */
export const API_ORIGIN = BASE.replace(/\/api$/, "");
export const DOCS_URL = `${API_ORIGIN}/docs`;

/**
 * Row key of a record. The backend assigns sequential integers (1..N, never reused), but a
 * value may arrive as a number (JSON) or as text (query strings, older payloads); always
 * compare and send keys through `uidKey`.
 */
export type Uid = string | number;

/** Canonical text of a row key ("12" for 12, "12", " 12 "); "" when there is none. */
export function uidKey(value: unknown): string {
  if (value == null) return "";
  return String(value).trim();
}

/** One record row as returned by the records/filter endpoints (UID is an integer there). */
export type RecordRow = Record<string, string | number | null> & { UID?: Uid | null };

/**
 * Translates an API error according to the active locale.
 *
 * The backend (FastAPI) HTTPException `detail` now returns a snake_case CODE
 * ("project_not_found") or, for dynamic information, the "code: extra-info" format
 * ("file_read_failed: <python error>"). `http()` puts this into Error.message as
 * `"<status>: <detail>"`.
 *
 * Known code → `t("errors.<code>")` (+ the extra info, if any). Unknown/network error
 * → the raw text or `errors.generic`. Use this instead of `e.message` in all catch
 * blocks so that TR/EN stays consistent.
 */
export function translateApiError(
  t: (k: string, v?: Record<string, string | number>) => string,
  e: unknown,
  fallbackKey = "errors.generic",
): string {
  const raw = e instanceof Error ? e.message : typeof e === "string" ? e : "";
  if (!raw) return t(fallbackKey);
  const afterStatus = raw.replace(/^\d{3}:\s*/, ""); // "404: x" → "x"
  const m = afterStatus.match(/^([a-z][a-z0-9_]*)(?::\s*([\s\S]*))?$/);
  if (m) {
    const key = `errors.${m[1]}`;
    const translated = t(key);
    if (translated !== key) {
      return m[2] ? `${translated}: ${m[2]}` : translated;
    }
  }
  return afterStatus || t(fallbackKey);
}

/**
 * Translates an Audit/Job title according to the locale. If the backend sends `title_key`
 * (+ `title_params`), it is translated with `t(key, params)`; otherwise the readable
 * `title` fallback is shown (for legacy records + the markdown report). AuditLogPanel,
 * Report and JobProgress use this.
 */
export function translateTitle(
  t: (k: string, v?: Record<string, string | number>) => string,
  obj: {
    title: string;
    title_key?: string | null;
    title_params?: Record<string, string | number> | null;
    kind?: string;
    details?: Record<string, unknown> | null;
  },
): string {
  // 1) If the backend explicitly sent a title_key (new records), use it.
  if (obj.title_key) {
    const v = t(obj.title_key, obj.title_params ?? undefined);
    if (v !== obj.title_key) return v;
  }
  // 2) If there is no title_key (old records from before the fix), rebuild from kind + details →
  //    so the old history also appears in the selected language (it does not get stuck in static TR).
  const derived = deriveAuditTitle(t, obj.kind, obj.details ?? undefined);
  if (derived != null) return derived;
  // 3) Last resort: the stored readable text.
  return obj.title;
}

/** Rebuilds old (title_key-less) audit records from kind + details according to the locale. */
function deriveAuditTitle(
  t: (k: string, v?: Record<string, string | number>) => string,
  kind?: string,
  details?: Record<string, unknown>,
): string | null {
  if (!kind) return null;
  const d = details ?? {};
  const s = (x: unknown) => String(x ?? "");
  switch (kind) {
    case "analysis_activate":
      return d.label != null ? t("audit.titles.analysisActivated", { label: s(d.label) }) : null;
    case "analysis_delete":
      return d.label != null ? t("audit.titles.analysisDeleted", { label: s(d.label) }) : null;
    case "export":
      if (d.deleted != null) return t("audit.titles.exportDeleted", { name: s(d.deleted) });
      if (d.output != null) return t("audit.titles.exported", { fmt: s(d.format).toUpperCase(), name: s(d.output) });
      return null;
    case "filter_save":
      if (d.action === "delete") return t("audit.titles.presetDeleted", { name: s(d.name) });
      return d.name != null ? t("audit.titles.presetSaved", { name: s(d.name) }) : null;
    case "upload":
      if (d.action === "delete") return t("audit.titles.fileDeleted", { name: s(d.name) });
      return Array.isArray(d.files) ? t("audit.titles.filesUploaded", { n: (d.files as unknown[]).length }) : null;
    case "convert":
      return d.output != null ? t("audit.titles.converted", { name: s(d.output) }) : null;
    case "merge":
      return t("audit.titles.merge", { method: s(d.method || "smart") });
    default:
      return null;
  }
}

export type Project = {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
  file_count: number;
  raw_size_bytes: number;
};

export type UploadedFile = {
  name: string;
  size: number;
  kind: "scopus_csv" | "wos_txt" | "xlsx" | "unknown";
  saved_path: string;
};

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(`${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  // Server health + active version (to show "which version is running" in the footer).
  health: () =>
    http<{
      status: string;
      version: string;
      codename: string;
      storage: string;
      disambiguation_enabled: boolean;
      frontend_bundled: boolean;
    }>("/health"),

  listProjects: () => http<Project[]>("/projects"),
  createProject: (name: string, description?: string) =>
    http<Project>("/projects", { method: "POST", body: JSON.stringify({ name, description }) }),
  getProject: (id: string) => http<Project>(`/projects/${id}`),
  deleteProject: (id: string) => http<void>(`/projects/${id}`, { method: "DELETE" }),

  listFiles: (id: string) => http<UploadedFile[]>(`/projects/${id}/files`),
  uploadFiles: (id: string, files: File[]) => {
    const fd = new FormData();
    for (const f of files) fd.append("files", f, f.name);
    return http<UploadedFile[]>(`/projects/${id}/files`, { method: "POST", body: fd });
  },
  deleteFile: (id: string, name: string) =>
    http<void>(`/projects/${id}/files/${encodeURIComponent(name)}`, { method: "DELETE" }),

  // Conversion
  listProcessed: (id: string) =>
    http<ConvertResult[]>(`/projects/${id}/convert/processed`),
  csvToXlsx: (id: string, files: string[], output = "scopus_merged.xlsx") =>
    http<ConvertResult>(`/projects/${id}/convert/csv-to-xlsx`, {
      method: "POST", body: JSON.stringify({ files, output }),
    }),
  wosToXlsx: (id: string, files: string[], output = "wos_merged.xlsx") =>
    http<ConvertResult>(`/projects/${id}/convert/wos-to-xlsx`, {
      method: "POST", body: JSON.stringify({ files, output }),
    }),
  xlsxToWosTxt: (id: string, file: string, output?: string) =>
    http<ConvertResult>(`/projects/${id}/convert/xlsx-to-wos-txt`, {
      method: "POST", body: JSON.stringify({ file, output }),
    }),
  xlsxToTsv: (id: string, file: string, output?: string) =>
    http<ConvertResult>(`/projects/${id}/convert/xlsx-to-tsv`, {
      method: "POST", body: JSON.stringify({ file, output }),
    }),
  downloadUrl: (id: string, filename: string) =>
    `${BASE}/projects/${id}/convert/download/${encodeURIComponent(filename)}`,

  // Merge — a single algorithm, Smart Merge (method is not sent; the backend ignores it)
  startMerge: (id: string) =>
    http<{ job_id: string }>(`/projects/${id}/merge`, {
      method: "POST", body: JSON.stringify({}),
    }),
  listBorderline: (id: string) =>
    http<BorderlinePair[]>(`/projects/${id}/merge/borderline`),
  decideBorderline: (id: string, decisions: { pair_id: string; decision: "accept" | "reject" | "skip" }[]) =>
    http<{ applied: number; snapshot: string | null; pending_after: number }>(
      `/projects/${id}/merge/borderline/decide`,
      { method: "POST", body: JSON.stringify({ decisions }) },
    ),
  listMerged: (id: string) =>
    http<ConvertResult[]>(`/projects/${id}/merge/results`),
  mergeSummary: (id: string) =>
    http<MergeSummary>(`/projects/${id}/merge/summary`),

  // Analyses — each merge has its own independent folder
  listAnalyses: (id: string) =>
    http<AnalysesListResponse>(`/projects/${id}/merge/analyses`),
  activateAnalysis: (id: string, analysisId: string) =>
    http<{ ok: boolean; active_id: string }>(
      `/projects/${id}/merge/analyses/${encodeURIComponent(analysisId)}/activate`,
      { method: "POST" },
    ),
  deleteAnalysis: (id: string, analysisId: string) =>
    http<{ ok: boolean; active_id: string | null }>(
      `/projects/${id}/merge/analyses/${encodeURIComponent(analysisId)}`,
      { method: "DELETE" },
    ),

  // Jobs
  getJob: (jobId: string) => http<JobInfo>(`/jobs/${jobId}`),
  listJobs: (projectId?: string, opts?: { active?: boolean }) => {
    const qs = new URLSearchParams();
    if (projectId) qs.set("project_id", projectId);
    if (opts?.active) qs.set("active", "true");
    const s = qs.toString();
    return http<JobInfo[]>(`/jobs${s ? `?${s}` : ""}`);
  },
  cancelJob: (jobId: string) =>
    http<{ ok: boolean }>(`/jobs/${jobId}/cancel`, { method: "POST" }),
  jobStreamUrl: (jobId: string) => `${BASE}/jobs/${jobId}/stream`,

  downloadFromUrl: (id: string, folder: "raw" | "processed" | "merged" | "exports" | "snapshots", filename: string) =>
    `${BASE}/projects/${id}/download/${folder}/${encodeURIComponent(filename)}`,

  // Filter
  filterRecords: (id: string, body: FilterRequest, signal?: AbortSignal) =>
    http<FilterResponse>(`/projects/${id}/filter`, {
      method: "POST", body: JSON.stringify(body), signal,
    }),
  // Apply the filter to the dataset PERMANENTLY — non-matching records are removed (a snapshot is taken),
  // subsequent steps (Harmonization/Export) work with the filtered data.
  applyFilter: (id: string, spec: FilterSpec) =>
    http<{ applied: boolean; kept: number; removed: number; total_before: number; snapshot: string | null }>(
      `/projects/${id}/filter/apply`,
      { method: "POST", body: JSON.stringify({ spec }) },
    ),
  listPresets: (id: string) => http<Preset[]>(`/projects/${id}/filter/presets`),
  savePreset: (id: string, name: string, spec: FilterSpec) =>
    http<{ ok: boolean }>(`/projects/${id}/filter/presets`, {
      method: "POST", body: JSON.stringify({ name, spec }),
    }),
  deletePreset: (id: string, name: string) =>
    http<void>(`/projects/${id}/filter/presets/${encodeURIComponent(name)}`, { method: "DELETE" }),

  // Export
  listExports: (id: string) => http<ConvertResult[]>(`/projects/${id}/export`),
  createExport: (id: string, fmt: ExportFormat, filter?: FilterSpec, output_name?: string, if_exists: "rename" | "replace" | "abort" = "rename") =>
    http<ConvertResult>(`/projects/${id}/export`, {
      method: "POST", body: JSON.stringify({ fmt, filter, output_name, if_exists }),
    }),
  deleteExport: (id: string, name: string) =>
    http<{ ok: boolean; deleted: string }>(`/projects/${id}/export/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),

  // — Standalone Tools (project'siz) —
  toolsFormats: () =>
    http<{
      source_formats: { id: string; label: string; extensions: string[] }[];
      target_formats: { id: string; label: string; extension: string }[];
    }>(`/tools/formats`),
  /** Upload a single file, convert it to the target format and return a download blob. */
  toolsConvert: async (file: File, source_format: string, target_format: string, output_name?: string, signal?: AbortSignal) => {
    const fd = new FormData();
    fd.append("file", file, file.name);
    fd.append("source_format", source_format);
    fd.append("target_format", target_format);
    if (output_name) fd.append("output_name", output_name);
    const res = await fetch(`${BASE}/tools/convert`, { method: "POST", body: fd, signal });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch {}
      throw new Error(`${res.status}: ${detail}`);
    }
    const blob = await res.blob();
    // Extract the filename from Content-Disposition
    const cd = res.headers.get("content-disposition") || "";
    const m = cd.match(/filename="?([^";]+)"?/);
    const filename = m ? m[1] : `converted_${Date.now()}`;
    return {
      blob,
      filename,
      records: parseInt(res.headers.get("x-records-count") || "0", 10),
      columns: parseInt(res.headers.get("x-columns-count") || "0", 10),
    };
  },

  // Prepare (auto CSV/TXT → XLSX)
  prepareStatus: (id: string) => http<PrepareReport>(`/projects/${id}/prepare/status`),
  prepare: (id: string) => http<PrepareReport>(`/projects/${id}/prepare`, { method: "POST" }),
  resetPrepare: (id: string) =>
    http<{ ok: boolean; deleted_processed: number }>(
      `/projects/${id}/prepare/reset`,
      { method: "POST" },
    ),
  deleteProcessedFile: (id: string, filename: string) =>
    http<void>(`/projects/${id}/prepare/processed/${encodeURIComponent(filename)}`, {
      method: "DELETE",
    }),

  // Export folder (local disk)
  suggestFolders: (id: string) => http<FolderSuggest>(`/projects/${id}/export-folder/suggest`),
  copyToFolder: (id: string, files: string[], target_folder: string) =>
    http<CopyReport>(`/projects/${id}/export-folder`, {
      method: "POST", body: JSON.stringify({ files, target_folder, create_if_missing: true }),
    }),

  // Enrichment
  startApiEnrich: (id: string) =>
    http<{ job_id: string }>(`/projects/${id}/enrich/api`, {
      method: "POST", body: JSON.stringify({}),
    }),
  // Unified fill — one API call per DOI, all empty fields (API only; no ML).
  startFillAll: (id: string) =>
    http<{ job_id: string }>(`/projects/${id}/enrich/fill-all`, { method: "POST" }),

  // Disambiguation
  disambiguateStatus: (id: string) =>
    http<DisambiguationStatus>(`/projects/${id}/disambiguate/status`),
  startAuthorDisambiguation: (id: string, mode: "auto" | "broad" | "full_llm" = "auto", max_records?: number | null) =>
    http<{ job_id: string; mode: string; max_records: number | null }>(`/projects/${id}/disambiguate/authors`, {
      method: "POST", body: JSON.stringify({ mode, max_records: max_records ?? null }),
    }),
  startAffiliationDisambiguation: (id: string, mode: "auto" | "broad" | "full_llm" = "auto", max_records?: number | null) =>
    http<{ job_id: string; mode: string; max_records: number | null }>(`/projects/${id}/disambiguate/affiliations`, {
      method: "POST", body: JSON.stringify({ mode, max_records: max_records ?? null }),
    }),
  startCountryStandardization: (id: string, mode: "auto" | "broad" | "full_llm" = "auto", max_records?: number | null) =>
    http<{ job_id: string; mode: string; max_records: number | null }>(`/projects/${id}/disambiguate/countries`, {
      method: "POST", body: JSON.stringify({ mode, max_records: max_records ?? null }),
    }),
  startOrgRollup: (id: string, mode: "auto" | "broad" | "full_llm" = "auto", max_records?: number | null) =>
    http<{ job_id: string; mode: string; max_records: number | null }>(`/projects/${id}/disambiguate/organizations`, {
      method: "POST", body: JSON.stringify({ mode, max_records: max_records ?? null }),
    }),
  getProposals: (id: string, kind: "authors" | "affiliations" | "countries" | "organizations") =>
    http<ProposalSet>(`/projects/${id}/disambiguate/proposals/${kind}`),
  applyClusters: (
    id: string, kind: "authors" | "affiliations" | "countries" | "organizations",
    approved: Array<Record<string, unknown>>,
    approvedSplits: Array<Record<string, unknown>> = [],
  ) =>
    http<ApplyResult>(`/projects/${id}/disambiguate/apply`, {
      method: "POST", body: JSON.stringify({ kind, approved, approved_splits: approvedSplits }),
    }),
  restoreSnapshot: (id: string, snapshot: string) =>
    http<{ restored_from: string }>(`/projects/${id}/disambiguate/restore`, {
      method: "POST", body: JSON.stringify({ snapshot }),
    }),

  // Quality / Charts
  qualityStats: (id: string) => http<QualityStats>(`/projects/${id}/quality/stats`),
  // Overview table download URL (CSV/XLSX) — used directly with <a href> / fetch.
  qualityOverviewUrl: (id: string, fmt: "csv" | "xlsx") => `${BASE}/projects/${id}/quality/overview?fmt=${fmt}`,
  lastFillReport: (id: string) => http<{ report: FillReport | null }>(`/projects/${id}/quality/last-fill-report`),
  // Per-record provenance CSV of the latest reverse DOI lookup (404 `doi_log_not_found` if none).
  doiLogUrl: (id: string) => `${BASE}/projects/${id}/enrich/doi-log`,
  qualityCharts: (id: string) => http<QualityCharts>(`/projects/${id}/quality/charts`),

  // One-time conversion of saved analyses to sequential row keys (runs in the background at startup).
  uidMigrationStatus: () => http<UidMigrationStatus>("/system/uid-migration"),

  // Records — delete & snapshots
  // Rows are addressed by UID only (no DOI / position keys). UIDs that are not in the
  // current dataset are skipped by the backend and counted in `not_found`.
  deleteRecords: (id: string, body: { uids: Uid[]; reason?: string }) =>
    http<{ deleted: number; kept: number; total_before: number; snapshot: string | null; not_found?: number }>(
      `/projects/${id}/records/delete`,
      { method: "POST", body: JSON.stringify(body) }
    ),
  listSnapshots: (id: string) =>
    http<Snapshot[]>(`/projects/${id}/records/snapshots`),
  restoreRecordSnapshot: (id: string, snapshot: string) =>
    http<{ restored: number; snapshot: string }>(`/projects/${id}/records/restore-snapshot`, {
      method: "POST", body: JSON.stringify({ snapshot }),
    }),
  // UID is mandatory; unknown UID -> 404 record_not_found; a "UID" key inside `fields` -> 400 uid_immutable.
  updateRecord: (id: string, body: { uid: Uid; fields: Record<string, string> }) =>
    http<{ updated: number; fields: number; snapshot: string | null }>(`/projects/${id}/records/update`, {
      method: "POST", body: JSON.stringify(body),
    }),

  // Audit log
  listAudit: (id: string, limit = 200) =>
    http<AuditEntry[]>(`/projects/${id}/audit?limit=${limit}`),
  auditSummary: (id: string) =>
    http<AuditSummary>(`/projects/${id}/audit/summary`),
  addAuditEntry: (
    id: string,
    entry: {
      kind: string;
      title: string;
      title_key?: string;
      title_params?: Record<string, unknown>;
      details?: Record<string, unknown>;
      user_action?: string;
    },
  ) =>
    http<AuditEntry>(`/projects/${id}/audit`, { method: "POST", body: JSON.stringify(entry) }),
  clearAudit: (id: string) =>
    http<void>(`/projects/${id}/audit`, { method: "DELETE" }),
  auditReportUrl: (id: string, lang?: string) =>
    `${BASE}/projects/${id}/audit/report.md${lang ? `?lang=${encodeURIComponent(lang)}` : ""}`,

  // Report (final step after Export) — raw log + LLM methodology report
  reportLogUrl: (id: string, fmt: "md" | "txt" | "pdf", lang?: string) =>
    `${BASE}/projects/${id}/report/log.${fmt}${lang ? `?lang=${encodeURIComponent(lang)}` : ""}`,
  // Data flow derived from the audit log (the Report page draws a diagram)
  reportFlow: (id: string) => http<ReportFlow>(`/projects/${id}/report/flow`),
  methodologyUrl: (id: string, fmt: "md" | "txt" | "pdf") =>
    `${BASE}/projects/${id}/report/methodology.${fmt}`,
  getMethodology: (id: string) =>
    http<MethodologyReport>(`/projects/${id}/report/methodology`),
  generateMethodology: (id: string) =>
    http<MethodologyReport>(`/projects/${id}/report/methodology`, { method: "POST" }),

  // Settings
  getSettings: () => http<SettingsResponse>("/settings"),
  updateSettings: (updates: Record<string, string | number | boolean>) =>
    http<SettingsResponse>("/settings", { method: "PUT", body: JSON.stringify({ updates }) }),
  validatePath: (path: string, create_if_missing = false) =>
    http<PathValidation>("/settings/validate-path", {
      method: "POST", body: JSON.stringify({ path, create_if_missing }),
    }),

  // System directory browser
  browseFolder: (path = "") =>
    http<BrowseResponse>(`/system/browse${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  // Open a folder known on the server side in the OS file manager (self-hosted:
  // server + browser on the same machine). The path is not sent from the client; the server resolves it.
  openFolder: (target: "storage" | "project", projectId?: string) =>
    http<{ opened: boolean; path: string }>("/system/open-folder", {
      method: "POST", body: JSON.stringify({ target, project_id: projectId ?? null }),
    }),
  findFolderByName: (name: string, limit = 8) =>
    http<{ name: string; matches: { path: string; depth: number; parent: string }[] }>(
      `/system/find-by-name?name=${encodeURIComponent(name)}&limit=${limit}`
    ),
};

export type ReportFlow = {
  analysis_id: string | null;
  has_merge: boolean;
  inputs: { wos: number; scopus: number; total: number } | null;
  intra_removed: number;
  matched_pairs: number;
  stages: Record<string, number>;
  borderline_total: number;
  after_merge: number | null;
  steps: { kind: string; removed: number; after: number | null; criteria: string[]; ts: number }[];
  final_total: number | null;
};

export type MethodologyReport = {
  text: string | null;
  generated_at?: number;
  model?: string;
  provider?: string;
  lang?: string;
  step_count?: number;
};

export type PathValidation = {
  valid: boolean;
  exists: boolean;
  writable: boolean;
  is_dir: boolean;
  absolute: boolean;
  resolved: string;
  project_count?: number;
  message: string;
};

export type BrowseEntry = {
  name: string;
  path: string;
  is_dir: boolean;
  is_drive: boolean;
};

export type BrowseShortcut = {
  /** Stable shortcut id (home, documents, downloads, ...) — the UI translates it. */
  id?: string;
  /** English fallback label. */
  label: string;
  path: string;
  icon: string;
};

export type BrowseResponse = {
  current: string;
  parent: string | null;
  is_root: boolean;
  entries: BrowseEntry[];
  shortcuts: BrowseShortcut[];
  platform: "windows" | "unix";
};

export type SettingField = {
  key: string;
  label: string;
  value: string;
  is_set: boolean;
  secret: boolean;
  group: string;
  type: "string" | "bool" | "float" | "path" | "provider" | "model";
  default?: string | null;
  hint?: string | null;
};

export type LLMProviderPreset = {
  id: string;
  label: string;
  base_url: string;
  models: { id: string; label: string }[];
  key_url: string;
};

export type SettingsResponse = {
  groups: Record<string, string>;
  fields: SettingField[];
  env_file: string;
  notes: string[];
  llm_providers: LLMProviderPreset[];
  /** The storage folder the server is using RIGHT NOW. */
  active_storage?: string;
  /** STORAGE_DIR in .env differs from the active one — pending a restart. */
  storage_pending_restart?: boolean;
};

export type QualityField = {
  field: string;
  label: string;
  hint: string;
  total: number;
  filled: number;
  missing: number;
  fill_rate: number;
  available: boolean;
};

export type QualityStats = {
  total_records: number;
  health_score: number;
  fields: QualityField[];
  db_distribution: Record<string, number>;
};

/** Reverse DOI lookup result of a 'Fill all' run. A record that was not matched with confidence is skipped, never guessed. */
export type DoiFillStats = {
  scanned?: number;
  filled?: number;
  skipped?: {
    ambiguous?: number;
    preprint_only?: number;
    duplicate?: number;
    type_rejected?: number;
    no_match?: number;
    api_error?: number;
    /** DOI written, then removed because the title returned by the DOI did not match the record. */
    suspect?: number;
  };
  /** Sources that hit a hard quota during the run (e.g. "openalex"). */
  sources_exhausted?: string[];
  /** Path of the per-record provenance CSV (download via api.doiLogUrl), or null. */
  provenance?: string | null;
};

/** Persistent summary of the last 'Fill all' operation (from the audit). */
export type FillReport = {
  ts?: number;
  enriched?: number;
  api?: { total?: number; enriched?: number };
  doi?: DoiFillStats;
  fill_rate_before?: number;
  fill_rate_after?: number;
  per_field_fill?: Record<string, { before: number; after: number }>;
  snapshot?: string | null;
};

export type QualityCharts = {
  year_histogram?: { year: number; count: number }[];
  year_by_doctype?: { types: string[]; data: Array<Record<string, number>> };
  top_journals?: { name: string; count: number }[];
  doc_types?: { name: string; count: number }[];
  languages?: { name: string; count: number }[];
  citation_buckets?: { bucket: string; count: number }[];
};

export type MergeFieldStat = {
  field: string;
  label: string;
  total: number;
  filled: number;
  missing: number;
  missing_pct: number;
  fill_rate: number;
  status: string;
};

export type MergeFile = {
  name: string;
  size: number;
  mtime: number;
  kind: "merged_dataset" | "lost_wos" | "lost_scopus" | "statistics"
      | "match_audit" | "conflict_log" | "borderline_queue" | "other";
  relative_path: string;
};

export type MergeSummary = {
  has_merge: boolean;
  /** Whether the raw data changed after the active merge (a new file was added → a re-merge is needed). */
  stale?: boolean;
  method?: "simple" | "enhanced" | "smart" | null;
  analysis?: {
    id: string;
    label?: string | null;
    method?: string | null;
    created_at?: number | null;
    completed_at?: number | null;
    is_active?: boolean;
  };
  general?: {
    total_records: number;
    wos_records: number;
    scopus_records: number;
    total_input: number;
    duplicates_removed: number;
    dedup_rate: number;
    merged_columns: number;
    common_columns: number;
  };
  fields?: MergeFieldStat[];
  files?: MergeFile[];
  lost_wos_count?: number;
  lost_scopus_count?: number;
  // Smart-specific fields
  match_stages?: Record<string, number>;
  conflict_count?: number;
  field_source_distribution?: Record<string, number>;
  borderline_pending?: number;
  borderline_total?: number;
};

export type AnalysisItem = {
  id: string;
  label: string;
  /** True when the backend generated `label` from method + time (the UI rebuilds it in the app language). */
  label_auto?: boolean | null;
  method: "simple" | "enhanced" | "smart" | "single" | "unknown" | string;
  created_at: number;
  completed_at?: number | null;
  file_count: number;
  total_size: number;
  source?: "user_run" | "legacy_migration" | string;
  is_active: boolean;
};

export type AnalysesListResponse = {
  active_id: string | null;
  items: AnalysisItem[];
};

export type BorderlinePair = {
  pair_id: string;
  /** Stable record identity of the two sides (sequential row keys of the merged dataset). */
  wos_uid?: Uid;
  scp_uid?: Uid;
  /** Legacy positional fields (old queues only); never used to address rows. */
  wos_index?: number;
  scp_index?: number;
  jw_title: number;
  confidence: number;
  status: "pending" | "accept" | "reject" | "skip";
  decided_at?: number | null;
  /** English technical explanation from the backend — tooltip only, never rendered as text. */
  reason: string;
  /** True when both records carry the same DOI written differently (backend flag, optional). */
  doi_variant?: boolean | null;
  /** Absolute publication-year difference computed by the backend (null when a year is missing). */
  year_diff?: number | null;
  /** First-author surname match computed by the backend (null when a surname is missing). */
  surname_match?: boolean | null;
  wos: {
    doi?: string | null;
    title: string;
    year?: number | null;
    surname?: string | null;
    journal?: string | null;
    volume?: string | null;
  };
  scopus: {
    doi?: string | null;
    title: string;
    year?: number | null;
    surname?: string | null;
    journal?: string | null;
    volume?: string | null;
  };
  llm_suggestion?: {
    verdict: "same" | "different" | "uncertain";
    confidence: number;
    reason: string;
  } | null;
};

export type Snapshot = {
  name: string;
  relative_path: string;
  size: number;
  mtime: number;
};

export type AuditEntry = {
  ts: number;
  kind: string;
  title: string;
  title_key?: string | null;
  title_params?: Record<string, string | number> | null;
  analysis_id?: string | null;
  details?: Record<string, unknown>;
  before?: Record<string, unknown> | null;
  after?: Record<string, unknown> | null;
  snapshot?: string | null;
  user_action?: string | null;
};

export type AuditSummary = {
  total: number;
  by_kind: Record<string, number>;
  first_ts: number | null;
  last_ts: number | null;
};

export type DisambiguationStatus = {
  enabled: boolean;
  configured: boolean;
  model: string;
  provider?: string;
  base_url?: string;
  blocking_threshold: number;
  auto_approve_threshold: number;
};

export type ClusterMember = {
  id: string;
  name_variants?: string[];
  variants?: string[];
  affiliations?: string[];
  coauthors?: string[];
  year_range?: string[];
  /** Row UIDs of the records this member appears in (never positions). */
  records?: Uid[];
};

export type Cluster = {
  cluster_id: string;
  block_key?: string;
  member_ids: string[];
  members: ClusterMember[];
  confidence: number;
  reason: string;
  canonical_name?: string;
  country?: string;
  tier?: number;          // 1 = deterministic (automatic), 2 = LLM
  source?: string;        // "deterministic" | "llm"
};

export type SplitGroup = {
  /** Row UIDs (never positions). */
  records: Uid[];
  fields: string[];
  suffix: string;         // "" = the largest group (stays plain); "(b)" "(c)" ...
};
export type AuthorSplit = {
  split_id: string;
  name: string;           // original spelling (e.g. "Mehmet A")
  norm?: string;
  tier: number;           // 1 = clear-cut (field-disjoint), 2 = borderline (AI)
  source?: string;        // "deterministic" | "llm" | "manual"
  decision?: string;      // "split" | "keep" | "review"
  confidence?: number | null;
  reason: string;
  groups: SplitGroup[];
};

export type ProcessedFile = {
  name: string;
  size: number;
  mtime: number;
  kind: "scopus" | "wos" | "other";
};

export type PrepareReport = {
  scopus_xlsx: string | null;
  wos_xlsx: string | null;
  raw_csv_count: number;
  raw_txt_count: number;
  skipped: string[];
  messages: string[];
  processed_files?: ProcessedFile[];
  stale?: boolean;
};

export type FolderSuggest = {
  home: string;
  suggestions: { label: string; path: string }[];
};

export type CopyReport = {
  target_folder: string;
  copied: string[];
  skipped: { name: string; reason: string }[];
};

/** Result of applying approved clusters / splits. */
export type ApplyResult = {
  replacements: number;
  snapshot: string | null;
  applied_rows?: number;
  /** UIDs of the proposal that were no longer in the dataset (skipped, never remapped). */
  skipped_missing?: number;
  /** First UIDs that were changed (capped by the backend). */
  changed_uids?: Uid[];
};

export type ProposalSet = {
  kind: string;
  /** "uid_v2" for proposal files addressed by sequential row keys. */
  schema?: string;
  analysis_id?: string | null;
  generated_at?: number;
  auto_approve_threshold?: number;
  clusters: Cluster[];
  splits?: AuthorSplit[];
  uncertain: { id?: string; reason: string; block_key?: string }[];
};

export type ExportFormat = "wos" | "vos" | "bib" | "ris" | "csv" | "xlsx" | "tsv";

export type Range = { min?: number | null; max?: number | null };

export type FilterSpec = {
  year?: Range;
  citation_count?: Range;
  doc_type?: string[];
  language?: string[];
  db_source?: string[];
  journal?: string[];
  authors?: string[];
  wc_categories?: string[];
  sc_categories?: string[];
  fulltext?: { query: string; fields?: string[] };
  quality?: { missing?: string[]; has?: string[] };
};

export type FilterRequest = {
  spec: FilterSpec;
  offset?: number;
  limit?: number;
  columns?: string[];
  include_facets?: boolean;
};

/** One analysis the migration could not convert (the analysis keeps working with its old keys). */
export type UidMigrationError = {
  project?: string | null;
  project_id?: string | null;
  project_name?: string | null;
  analysis?: string | null;
  analysis_id?: string | null;
  analysis_name?: string | null;
  message?: string | null;
  error?: string | null;
};

/** Progress of the one-time record migration (GET /api/system/uid-migration). */
export type UidMigrationStatus = {
  state: "idle" | "running" | "done" | "error";
  projects_total?: number;
  projects_done?: number;
  analyses_done?: number;
  current_project_name?: string | null;
  /** A single failure text (whole run failed). */
  error?: string | null;
  /** Per-analysis failures; the run continues past them. */
  errors?: Array<string | UidMigrationError> | null;
};

export type FilterResponse = {
  total: number;
  offset: number;
  limit: number;
  columns: string[];
  records: RecordRow[];
  facets?: Facets;
  facets_all?: Facets;
};

export type Facets = {
  total: number;
  year?: { min: number; max: number; histogram: { year: number; count: number }[] };
  citation_count?: { min: number; max: number; mean: number };
  doc_type?: { value: string; count: number }[];
  language?: { value: string; count: number }[];
  db_source?: { value: string; count: number }[];
  journal_top?: { value: string; count: number }[];
};

export type Preset = { name: string; spec: FilterSpec };

export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export type JobInfo = {
  id: string;
  project_id: string;
  kind: string;
  title: string;
  title_key?: string | null;
  title_params?: Record<string, string | number> | null;
  /** Exclusive jobs lock dataset-mutating endpoints of the project while they run. */
  exclusive?: boolean;
  status: JobStatus;
  progress: number;
  log_tail: string[];
  /** Translatable one-line status: show t(status_key, status_params); fallback = last log line. */
  status_key?: string | null;
  status_params?: Record<string, string | number> | null;
  result: unknown;
  error: string | null;
  /** Leading snake_case code of `error` (e.g. "no_merged_data"), or null. */
  error_code?: string | null;
  started_at: number | null;
  finished_at: number | null;
  created_at: number;
};

export type ConvertResult = {
  name: string;
  size: number;
  relative_path: string;
};

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}
