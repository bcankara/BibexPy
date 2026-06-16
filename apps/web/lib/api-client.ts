/**
 * API base URL — RUNTIME origin tespiti (build-time env'e bağlı DEĞİL).
 *
 * Neden runtime: static export tek bir build'den üretilir ama iki farklı
 * senaryoda çalışır — (1) `npm run dev` ayrı backend (port 8001), (2) `bibexpy`
 * paketi backend ile AYNI origin'de (herhangi bir port). Build-time
 * NEXT_PUBLIC_API_BASE kullanmak, .env.local'daki dev adresinin (localhost:8001)
 * pakete gömülüp "Failed to fetch" hatasına yol açmasına neden oluyordu.
 *
 * Kural:
 *   • Next dev server portu (3000–3009) → ayrı backend http://<host>:8001/api
 *   • Diğer her durum (paket modu, herhangi bir port) → aynı origin "/api"
 *   • SSR/prerender (window yok) → "/api" (fetch'ler zaten client-side)
 *
 * Backend tüm router'ları `/api/...` altında mount eder.
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
 * API origin (BASE'den `/api` çıkarılmış hali) ve Swagger docs URL'i — aynı
 * runtime mantığı. Paket modunda relative ("" / "/docs"), dev'de mutlak
 * (http://host:8001/docs). AppShell "API" linki bunu kullanır.
 */
export const API_ORIGIN = BASE.replace(/\/api$/, "");
export const DOCS_URL = `${API_ORIGIN}/docs`;

/**
 * API hatasını aktif locale'e göre çevirir.
 *
 * Backend (FastAPI) HTTPException `detail`'i artık snake_case bir KOD döndürür
 * ("project_not_found") veya dinamik bilgi için "kod: ek-bilgi" formatında
 * ("file_read_failed: <python error>"). `http()` bunu Error.message'a
 * `"<status>: <detail>"` olarak koyar.
 *
 * Bilinen kod → `t("errors.<code>")` (+ varsa ek-bilgi). Bilinmeyen/ağ hatası
 * → ham metin veya `errors.generic`. Tüm catch bloklarında `e.message` yerine
 * bunu kullanın ki TR/EN tutarlı olsun.
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
 * Audit/Job başlığını locale'e göre çevirir. Backend `title_key` (+ `title_params`)
 * gönderirse `t(key, params)` ile çevrilir; yoksa okunabilir `title` fallback'i
 * gösterilir (legacy kayıtlar + markdown rapor için). AuditLogPanel, Report ve
 * JobProgress bunu kullanır.
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
  // 1) Backend açıkça title_key gönderdiyse (yeni kayıtlar) onu kullan.
  if (obj.title_key) {
    const v = t(obj.title_key, obj.title_params ?? undefined);
    if (v !== obj.title_key) return v;
  }
  // 2) title_key yoksa (fix öncesi eski kayıtlar) kind + details'ten yeniden kur →
  //    böylece eski geçmiş de seçilen dilde görünür (statik TR'de takılı kalmaz).
  const derived = deriveAuditTitle(t, obj.kind, obj.details ?? undefined);
  if (derived != null) return derived;
  // 3) Son çare: kaydedilmiş okunabilir metin.
  return obj.title;
}

/** Eski (title_key'siz) audit kayıtlarını kind + details'ten locale'e göre kurar. */
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
  // Sunucu sağlığı + aktif sürüm (footer'da "hangi sürüm çalışıyor" göstermek için).
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

  // Merge — tek algoritma Smart Merge (method gönderilmez; backend yok sayar)
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

  // Analizler — her birleştirme bağımsız klasör
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
  listJobs: (projectId?: string) =>
    http<JobInfo[]>(`/jobs${projectId ? `?project_id=${projectId}` : ""}`),
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
  /** Tek dosya yükle, hedef formata dönüştür ve indirme blob'u döndür. */
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
    // Filename'i Content-Disposition'dan çek
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

  // Export folder (lokal disk)
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
  // Birleşik doldurma — DOI başına tek API çağrısı, tüm boş alanlar (yalnız API; ML yoktur).
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
    http<{ replacements: number; snapshot: string | null }>(`/projects/${id}/disambiguate/apply`, {
      method: "POST", body: JSON.stringify({ kind, approved, approved_splits: approvedSplits }),
    }),
  restoreSnapshot: (id: string, snapshot: string) =>
    http<{ restored_from: string }>(`/projects/${id}/disambiguate/restore`, {
      method: "POST", body: JSON.stringify({ snapshot }),
    }),

  // Quality / Charts
  qualityStats: (id: string) => http<QualityStats>(`/projects/${id}/quality/stats`),
  // Genel Bakış tablosu indirme URL'i (CSV/XLSX) — doğrudan <a href> / fetch ile kullanılır.
  qualityOverviewUrl: (id: string, fmt: "csv" | "xlsx") => `${BASE}/projects/${id}/quality/overview?fmt=${fmt}`,
  lastFillReport: (id: string) => http<{ report: FillReport | null }>(`/projects/${id}/quality/last-fill-report`),
  qualityCharts: (id: string) => http<QualityCharts>(`/projects/${id}/quality/charts`),

  // Records — delete & snapshots
  deleteRecords: (id: string, body: { uids?: string[]; dois?: string[]; indices?: number[]; reason?: string }) =>
    http<{ deleted: number; kept: number; total_before: number; snapshot: string | null }>(
      `/projects/${id}/records/delete`,
      { method: "POST", body: JSON.stringify(body) }
    ),
  listSnapshots: (id: string) =>
    http<Snapshot[]>(`/projects/${id}/records/snapshots`),
  restoreRecordSnapshot: (id: string, snapshot: string) =>
    http<{ restored: number; snapshot: string }>(`/projects/${id}/records/restore-snapshot`, {
      method: "POST", body: JSON.stringify({ snapshot }),
    }),
  updateRecord: (id: string, body: { uid?: string; doi?: string; fields: Record<string, string> }) =>
    http<{ updated: number; fields: number; snapshot: string | null }>(`/projects/${id}/records/update`, {
      method: "POST", body: JSON.stringify(body),
    }),

  // Audit log
  listAudit: (id: string, limit = 200) =>
    http<AuditEntry[]>(`/projects/${id}/audit?limit=${limit}`),
  auditSummary: (id: string) =>
    http<AuditSummary>(`/projects/${id}/audit/summary`),
  addAuditEntry: (id: string, entry: { kind: string; title: string; details?: Record<string, unknown>; user_action?: string }) =>
    http<AuditEntry>(`/projects/${id}/audit`, { method: "POST", body: JSON.stringify(entry) }),
  clearAudit: (id: string) =>
    http<void>(`/projects/${id}/audit`, { method: "DELETE" }),
  auditReportUrl: (id: string) => `${BASE}/projects/${id}/audit/report.md`,

  // Rapor (Export sonrası son adım) — ham günlük + LLM metodoloji raporu
  reportLogUrl: (id: string, fmt: "md" | "txt" | "pdf") =>
    `${BASE}/projects/${id}/report/log.${fmt}`,
  methodologyUrl: (id: string, fmt: "md" | "txt" | "pdf") =>
    `${BASE}/projects/${id}/report/methodology.${fmt}`,
  getMethodology: (id: string) =>
    http<MethodologyReport>(`/projects/${id}/report/methodology`),
  generateMethodology: (id: string) =>
    http<MethodologyReport>(`/projects/${id}/report/methodology`, { method: "POST" }),

  // Ayarlar
  getSettings: () => http<SettingsResponse>("/settings"),
  updateSettings: (updates: Record<string, string | number | boolean>) =>
    http<SettingsResponse>("/settings", { method: "PUT", body: JSON.stringify({ updates }) }),
  validatePath: (path: string, create_if_missing = false) =>
    http<PathValidation>("/settings/validate-path", {
      method: "POST", body: JSON.stringify({ path, create_if_missing }),
    }),

  // Sistem dizin gezgini
  browseFolder: (path = "") =>
    http<BrowseResponse>(`/system/browse${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  findFolderByName: (name: string, limit = 8) =>
    http<{ name: string; matches: { path: string; depth: number; parent: string }[] }>(
      `/system/find-by-name?name=${encodeURIComponent(name)}&limit=${limit}`
    ),
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

/** Son 'Fill all' işleminin kalıcı özeti (audit'ten). */
export type FillReport = {
  ts?: number;
  enriched?: number;
  api?: { total?: number; enriched?: number };
  doi?: { scanned?: number; filled?: number };
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
  /** Ham veriler aktif merge'den sonra değişti mi (yeni dosya eklendi → yeniden merge gerekir). */
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
  // Smart-özel alanlar
  match_stages?: Record<string, number>;
  conflict_count?: number;
  field_source_distribution?: Record<string, number>;
  borderline_pending?: number;
  borderline_total?: number;
};

export type AnalysisItem = {
  id: string;
  label: string;
  method: "simple" | "enhanced" | "smart" | "unknown" | string;
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
  wos_index: number;
  scp_index: number;
  jw_title: number;
  confidence: number;
  status: "pending" | "accept" | "reject" | "skip";
  decided_at?: number | null;
  reason: string;
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
  records?: number[];
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
  tier?: number;          // 1 = deterministik (otomatik), 2 = LLM
  source?: string;        // "deterministic" | "llm"
};

export type SplitGroup = {
  records: number[];
  fields: string[];
  suffix: string;         // "" = en büyük grup (düz kalır); "(b)" "(c)" ...
};
export type AuthorSplit = {
  split_id: string;
  name: string;           // orijinal yazılış (örn. "Mehmet A")
  norm?: string;
  tier: number;           // 1 = net (alan-ayrık), 2 = sınırda (AI)
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

export type ProposalSet = {
  kind: string;
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

export type FilterResponse = {
  total: number;
  offset: number;
  limit: number;
  columns: string[];
  records: Record<string, string | null>[];
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
  status: JobStatus;
  progress: number;
  log_tail: string[];
  result: unknown;
  error: string | null;
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
