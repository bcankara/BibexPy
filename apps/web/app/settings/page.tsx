"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, type LLMProviderPreset, type PathValidation, type SettingField, type SettingsResponse, translateApiError} from "@/lib/api-client";
import { InfoTip } from "@/components/InfoTip";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { Button } from "@/components/Button";
import { PageHeader } from "@/components/PageHeader";
import { FolderPickerModal } from "@/components/FolderPickerModal";
import {
  Save, Eye, EyeOff, Database, Sparkles, AlertCircle, CheckCircle2, RotateCcw, Info, KeyRound,
  FolderOpen, HardDrive, Loader2, ExternalLink, Cpu, FolderInput, Languages,
} from "lucide-react";
import { useT, useI18n, type Locale } from "@/lib/i18n";
import { cn } from "@/lib/cn";

type TFn = (k: string, v?: Record<string, string | number>) => string;

/**
 * Backend'den gelen metni (group label, field label/hint) frontend i18n ile
 * çevirir. Backend stabil ANAHTAR (group key / field.key) gönderir; locale
 * frontend'de olduğu için çeviri burada yapılır. i18n'de karşılık yoksa
 * backend metnine (fallback) düşülür → bilinmeyen alanlar yine de görünür.
 */
function trf(t: TFn, key: string, fallback?: string | null): string {
  const v = t(key);
  return v === key ? (fallback ?? key) : v;
}

/** Path doğrulama durumunu locale'e göre metne çevirir (backend message yerine). */
function pathStatusMessage(t: TFn, v: PathValidation): string {
  if (v.valid) return t("settings.pathStatus.ready");
  if (v.exists && !v.is_dir) return t("settings.pathStatus.notDir");
  if (v.exists) return t("settings.pathStatus.notWritable");
  return t("settings.pathStatus.missing");
}

export default function SettingsPage() {
  const t = useT();
  const { locale, setLocale, tArr } = useI18n();
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [touched, setTouched] = useState<Set<string>>(new Set());
  const [reveal, setReveal] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await api.getSettings();
      setData(d);
      const m: Record<string, string> = {};
      for (const f of d.fields) m[f.key] = f.value;
      setValues(m);
      setTouched(new Set());
    } catch (e) {
      setError(translateApiError(t, e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  function setVal(key: string, val: string) {
    setValues((prev) => ({ ...prev, [key]: val }));
    setTouched((prev) => new Set(prev).add(key));
  }

  async function save() {
    if (touched.size === 0) return;
    setSaving(true);
    setError(null);
    try {
      const updates: Record<string, string | boolean | number> = {};
      for (const k of touched) {
        const f = data?.fields.find((x) => x.key === k);
        if (!f) continue;
        const v = values[k] ?? "";
        if (f.type === "bool") {
          updates[k] = v === "true" || v === "1" || v.toLowerCase() === "evet";
        } else if (f.type === "float") {
          const n = parseFloat(v);
          if (!Number.isNaN(n)) updates[k] = n;
        } else {
          updates[k] = v;
        }
      }
      await api.updateSettings(updates);
      setSavedAt(Date.now());
      // 3sn sonra reload (server side maskelemeyi tazelemek için)
      setTimeout(reload, 600);
    } catch (e) {
      setError(translateApiError(t, e, "settings.saveFailed"));
    } finally {
      setSaving(false);
    }
  }

  function reset() {
    if (!data) return;
    const m: Record<string, string> = {};
    for (const f of data.fields) m[f.key] = f.value;
    setValues(m);
    setTouched(new Set());
  }

  const dirty = touched.size > 0;

  // Grup bazlı alanlar
  const grouped: Record<string, SettingField[]> = {};
  if (data) {
    for (const f of data.fields) {
      grouped[f.group] = grouped[f.group] ?? [];
      grouped[f.group].push(f);
    }
  }

  return (
    <>
      <PageHeader
        title={t("settings.title")}
        subtitle={t("settings.subtitle")}
        right={
          <div className="flex items-center gap-2">
            <Link href="/projects">
              <button className="text-xs text-white/90 hover:text-white px-3 py-1.5 rounded-md bg-white/10 hover:bg-white/20">
                {t("nav.projects")}
              </button>
            </Link>
          </div>
        }
      />

      <div className="max-w-3xl mx-auto px-6 py-6 space-y-4">
        {error && (
          <div className="rounded-lg border border-danger/30 bg-danger-soft px-4 py-2 text-sm text-red-700 flex items-center gap-2">
            <AlertCircle className="h-4 w-4" /> {error}
          </div>
        )}
        {savedAt && (
          <div className="rounded-lg border border-success/30 bg-success-soft px-4 py-2 text-sm text-emerald-700 flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4" /> {t("settings.saved")}
          </div>
        )}

        {/* Dil seçimi — frontend tarafında çalışır */}
        <Card>
          <CardHeader>
            <Languages className="h-4 w-4 text-brand-600" />
            <h2 className="font-semibold text-sm flex-1">{t("settings.language")}</h2>
          </CardHeader>
          <CardBody className="py-3">
            <p className="text-xs text-muted mb-3">{t("settings.languageHint")}</p>
            <div className="flex items-center gap-2">
              {(["en", "tr"] as Locale[]).map((l) => (
                <button
                  key={l}
                  onClick={() => setLocale(l)}
                  className={cn(
                    "px-4 py-2 rounded-md text-sm font-medium transition border",
                    locale === l
                      ? "bg-brand-500 text-white border-brand-500"
                      : "bg-white text-ink border-border hover:border-border-strong"
                  )}
                >
                  {l === "en" ? t("settings.english") : t("settings.turkish")}
                </button>
              ))}
            </div>
          </CardBody>
        </Card>

        {/* Notlar — backend yerine frontend i18n'den (locale'e göre TR/EN) */}
        {data && (
          <Card>
            <CardBody className="py-3 space-y-1.5">
              {tArr("settings.infoNotes").map((n, i) => (
                <div key={i} className="text-xs text-muted flex items-start gap-1.5">
                  <Info className="h-3.5 w-3.5 text-info mt-0.5 flex-shrink-0" />
                  <span>{n}</span>
                </div>
              ))}
              {data.env_file && (
                <div className="text-[10px] text-muted/70 font-mono mt-1.5">
                  .env: {data.env_file}
                </div>
              )}
            </CardBody>
          </Card>
        )}

        {/* Gruplar */}
        {loading ? (
          <Card>
            <CardBody className="text-center py-12 text-muted text-sm">{t("common.loading")}</CardBody>
          </Card>
        ) : data && Object.keys(grouped).map((groupKey) => (
          <Card key={groupKey}>
            <CardHeader>
              {groupKey === "storage" ? <HardDrive className="h-4 w-4 text-brand-500" /> :
               groupKey === "api" ? <Database className="h-4 w-4 text-brand-500" /> :
               <Sparkles className="h-4 w-4 text-brand-500" />}
              <h2 className="font-semibold text-sm">{trf(t, `settings.groups.${groupKey}`, data.groups[groupKey] ?? groupKey)}</h2>
            </CardHeader>
            <CardBody className="space-y-3">
              {groupKey === "llm" ? (
                <LLMSection
                  fields={grouped[groupKey]}
                  values={values}
                  touched={touched}
                  reveal={reveal}
                  providers={data.llm_providers}
                  onChange={setVal}
                  onToggleReveal={(key) => {
                    setReveal((prev) => {
                      const n = new Set(prev);
                      if (n.has(key)) n.delete(key); else n.add(key);
                      return n;
                    });
                  }}
                />
              ) : grouped[groupKey].map((f) => (
                f.key === "STORAGE_DIR" ? (
                  <PathField
                    key={f.key}
                    field={f}
                    value={values[f.key] ?? ""}
                    touched={touched.has(f.key)}
                    onChange={(v) => setVal(f.key, v)}
                  />
                ) : (
                  <FieldRow
                    key={f.key}
                    field={f}
                    value={values[f.key] ?? ""}
                    touched={touched.has(f.key)}
                    revealed={reveal.has(f.key)}
                    onChange={(v) => setVal(f.key, v)}
                    onToggleReveal={() => {
                      setReveal((prev) => {
                        const n = new Set(prev);
                        if (n.has(f.key)) n.delete(f.key); else n.add(f.key);
                        return n;
                      });
                    }}
                  />
                )
              ))}
            </CardBody>
          </Card>
        ))}

        {/* Save / Reset */}
        <div className="sticky bottom-4 z-10">
          <div className={cn(
            "rounded-xl border bg-white shadow-soft px-4 py-3 flex items-center gap-3 transition-all",
            dirty ? "border-brand-500" : "border-border",
          )}>
            <span className="text-sm">
              {dirty ? (
                <span className="text-ink font-medium tabular-nums">{t("settings.changesPending", { n: touched.size })}</span>
              ) : (
                <span className="text-muted">{t("settings.allSaved")}</span>
              )}
            </span>
            <div className="ml-auto flex items-center gap-2">
              {dirty && (
                <Button variant="secondary" size="sm" onClick={reset} disabled={saving}>
                  <RotateCcw className="h-3.5 w-3.5" /> {t("settings.revert")}
                </Button>
              )}
              <Button onClick={save} disabled={!dirty || saving}>
                <Save className="h-4 w-4" />
                {saving ? t("settings.saving") : t("common.save")}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

function LLMSection({ fields, values, touched, reveal, providers, onChange, onToggleReveal }: {
  fields: SettingField[];
  values: Record<string, string>;
  touched: Set<string>;
  reveal: Set<string>;
  providers: LLMProviderPreset[];
  onChange: (key: string, val: string) => void;
  onToggleReveal: (key: string) => void;
}) {
  const t = useT();
  const providerId = values["LLM_PROVIDER"] || "deepseek";
  const provider = useMemo(
    () => providers.find((p) => p.id === providerId) ?? providers[0],
    [providers, providerId],
  );
  const apiKeyField = fields.find((f) => f.key === "LLM_API_KEY");
  const baseUrlField = fields.find((f) => f.key === "LLM_BASE_URL");
  const modelField = fields.find((f) => f.key === "LLM_MODEL");
  const enabledField = fields.find((f) => f.key === "DISAMBIGUATION_ENABLED");
  const blockingField = fields.find((f) => f.key === "DISAMBIGUATION_BLOCKING_THRESHOLD");
  const autoApproveField = fields.find((f) => f.key === "DISAMBIGUATION_AUTO_APPROVE_THRESHOLD");

  function setProvider(newId: string) {
    onChange("LLM_PROVIDER", newId);
    const np = providers.find((p) => p.id === newId);
    if (!np) return;
    // Provider değişince base_url ve model'i preset'e güncelle
    if (np.id !== "custom") {
      onChange("LLM_BASE_URL", np.base_url);
      if (np.models.length > 0) {
        // Mevcut model bu provider'da var mı kontrol et, yoksa ilkini al
        const currentModel = values["LLM_MODEL"] || "";
        const exists = np.models.some((m) => m.id === currentModel);
        if (!exists) onChange("LLM_MODEL", np.models[0].id);
      }
    }
  }

  const isCustom = providerId === "custom";
  const modelInList = !!provider?.models.find((m) => m.id === (values["LLM_MODEL"] || ""));

  return (
    <div className="space-y-4">
      {/* Provider seçimi */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-2">
          <Cpu className="h-3.5 w-3.5 text-muted" />
          <label className="text-sm font-medium text-ink flex-1">{t("settings.llmProvider")}</label>
          <span className="text-[10px] text-muted font-mono">LLM_PROVIDER</span>
          {touched.has("LLM_PROVIDER") && (
            <span className="text-[10px] text-warning bg-warning-soft px-1.5 py-0.5 rounded-full">{t("settings.changed")}</span>
          )}
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-1.5">
          {providers.map((p) => (
            <button
              key={p.id}
              onClick={() => setProvider(p.id)}
              className={cn(
                "rounded-lg border px-3 py-2 text-xs font-medium transition text-left",
                providerId === p.id
                  ? "border-brand-500 bg-brand-50 text-brand-700 ring-2 ring-brand-500/20"
                  : "border-border bg-white text-ink hover:border-brand-300",
              )}
            >
              <div>{trf(t, `settings.providers.${p.id}`, p.label)}</div>
              {p.id === providerId && p.key_url && (
                <a
                  href={p.key_url}
                  target="_blank" rel="noreferrer"
                  className="text-[10px] text-brand-600 hover:underline mt-0.5 inline-flex items-center gap-0.5"
                  onClick={(e) => e.stopPropagation()}
                >
                  {t("settings.getApiKey")} <ExternalLink className="h-2.5 w-2.5" />
                </a>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Base URL — sadece custom seçilirse veya gerekirse görünür */}
      {isCustom && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <label className="text-sm font-medium text-ink flex-1">Base URL</label>
            <span className="text-[10px] text-muted font-mono">LLM_BASE_URL</span>
          </div>
          <input
            type="text"
            value={values["LLM_BASE_URL"] ?? ""}
            onChange={(e) => onChange("LLM_BASE_URL", e.target.value)}
            placeholder="https://api.together.ai/v1"
            className={cn(
              "w-full rounded-md border bg-white px-3 py-1.5 text-sm font-mono focus:outline-none focus:ring-2 transition",
              touched.has("LLM_BASE_URL") ? "border-brand-500 ring-brand-500/20" : "border-border focus:border-brand-500",
            )}
          />
          <p className="text-[11px] text-muted">{t("settings.baseUrlHint")}</p>
        </div>
      )}
      {!isCustom && baseUrlField && (
        <div className="text-[11px] text-muted px-1">
          Base URL: <span className="font-mono">{provider?.base_url}</span>
        </div>
      )}

      {/* Model seçimi */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-2">
          <label className="text-sm font-medium text-ink flex-1">{t("settings.modelLabel")}</label>
          <span className="text-[10px] text-muted font-mono">LLM_MODEL</span>
          {touched.has("LLM_MODEL") && (
            <span className="text-[10px] text-warning bg-warning-soft px-1.5 py-0.5 rounded-full">{t("settings.changed")}</span>
          )}
        </div>
        {provider && provider.models.length > 0 && !isCustom ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
            {provider.models.map((m) => {
              const sel = (values["LLM_MODEL"] || "") === m.id;
              return (
                <button
                  key={m.id}
                  onClick={() => onChange("LLM_MODEL", m.id)}
                  className={cn(
                    "text-left rounded-lg border px-3 py-2 text-xs transition",
                    sel
                      ? "border-brand-500 bg-brand-50 text-brand-700 ring-1 ring-brand-500/20"
                      : "border-border bg-white text-ink hover:border-brand-300",
                  )}
                >
                  <div className="font-mono text-[11px] font-semibold">{m.id}</div>
                  <div className="text-[10px] text-muted mt-0.5">{m.label}</div>
                </button>
              );
            })}
          </div>
        ) : (
          <input
            type="text"
            value={values["LLM_MODEL"] ?? ""}
            onChange={(e) => onChange("LLM_MODEL", e.target.value)}
            placeholder={t("settings.modelPlaceholder")}
            className={cn(
              "w-full rounded-md border bg-white px-3 py-1.5 text-sm font-mono focus:outline-none focus:ring-2 transition",
              touched.has("LLM_MODEL") ? "border-brand-500 ring-brand-500/20" : "border-border focus:border-brand-500",
            )}
          />
        )}
        {!isCustom && !modelInList && values["LLM_MODEL"] && (
          <p className="text-[11px] text-warning">
            ⚠ {t("settings.modelNotInList")}
          </p>
        )}
      </div>

      {/* API key */}
      {apiKeyField && (
        <FieldRow
          field={apiKeyField}
          value={values[apiKeyField.key] ?? ""}
          touched={touched.has(apiKeyField.key)}
          revealed={reveal.has(apiKeyField.key)}
          onChange={(v) => onChange(apiKeyField.key, v)}
          onToggleReveal={() => onToggleReveal(apiKeyField.key)}
        />
      )}

      {/* Disambiguation settings */}
      <div className="border-t border-border pt-3 space-y-3">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-muted">
          {t("settings.disambiguationPipeline")}
        </p>
        {enabledField && (
          <FieldRow
            field={enabledField}
            value={values[enabledField.key] ?? ""}
            touched={touched.has(enabledField.key)}
            revealed={false}
            onChange={(v) => onChange(enabledField.key, v)}
            onToggleReveal={() => {}}
          />
        )}
        {blockingField && (
          <FieldRow
            field={blockingField}
            value={values[blockingField.key] ?? ""}
            touched={touched.has(blockingField.key)}
            revealed={false}
            onChange={(v) => onChange(blockingField.key, v)}
            onToggleReveal={() => {}}
            helpText={t("help.blockingThreshold")}
          />
        )}
        {autoApproveField && (
          <FieldRow
            field={autoApproveField}
            value={values[autoApproveField.key] ?? ""}
            touched={touched.has(autoApproveField.key)}
            revealed={false}
            onChange={(v) => onChange(autoApproveField.key, v)}
            onToggleReveal={() => {}}
            helpText={t("help.autoApprove")}
          />
        )}
      </div>
    </div>
  );
}


function PathField({ field, value, touched, onChange }: {
  field: SettingField;
  value: string;
  touched: boolean;
  onChange: (v: string) => void;
}) {
  const t = useT();
  const [validation, setValidation] = useState<PathValidation | null>(null);
  const [checking, setChecking] = useState(false);
  const [autoChecked, setAutoChecked] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  // Mount'ta mevcut path'i otomatik validate et
  useEffect(() => {
    if (autoChecked || !value.trim()) return;
    setAutoChecked(true);
    api.validatePath(value).then(setValidation).catch(() => {});
  }, [value, autoChecked]);

  async function check(createIfMissing = false) {
    if (!value.trim()) return;
    setChecking(true);
    try {
      const r = await api.validatePath(value, createIfMissing);
      setValidation(r);
    } catch {
      setValidation(null);
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <FolderOpen className="h-3.5 w-3.5 text-muted" />
        <label className="text-sm font-medium text-ink flex-1">{trf(t, `settings.fields.${field.key}.label`, field.label)}</label>
        <span className="text-[10px] text-muted font-mono">{field.key}</span>
        {touched && (
          <span className="text-[10px] text-warning bg-warning-soft px-1.5 py-0.5 rounded-full">{t("settings.changed")}</span>
        )}
      </div>
      <div className="flex gap-2">
        <input
          type="text"
          value={value}
          onChange={(e) => { onChange(e.target.value); setValidation(null); }}
          placeholder={field.default ?? t("settings.pathPlaceholder")}
          className={cn(
            "flex-1 rounded-md border bg-white px-3 py-1.5 text-sm font-mono focus:outline-none focus:ring-2 transition",
            touched ? "border-brand-500 ring-brand-500/20" : "border-border focus:border-brand-500 focus:ring-brand-500/20",
          )}
        />
        <Button
          variant="secondary"
          size="sm"
          onClick={() => setPickerOpen(true)}
          title={t("settings.browseFolder")}
        >
          <FolderInput className="h-3.5 w-3.5" />
          {t("settings.browseFolder")}
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => check(false)}
          disabled={!value.trim() || checking}
          title={t("settings.validatePath")}
        >
          {checking ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
          {t("settings.check")}
        </Button>
      </div>

      <FolderPickerModal
        open={pickerOpen}
        initialPath={validation?.exists ? value : ""}
        onClose={() => setPickerOpen(false)}
        onSelect={(p) => { onChange(p); setValidation(null); setAutoChecked(false); }}
      />

      {validation && (
        <div className={cn(
          "rounded-lg border px-3 py-2 text-xs space-y-1",
          validation.valid ? "border-success/40 bg-success-soft/40 text-emerald-900" :
          validation.exists ? "border-warning/40 bg-warning-soft/40 text-amber-900" :
          "border-danger/40 bg-danger-soft/40 text-red-900",
        )}>
          <div className="flex items-center gap-1.5">
            {validation.valid ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertCircle className="h-3.5 w-3.5" />}
            <span className="font-medium">{pathStatusMessage(t, validation)}</span>
            {validation.valid && validation.project_count != null && (
              <span className="ml-auto text-[10px]">
                {t("settings.nProjectsExist", { n: validation.project_count })}
              </span>
            )}
          </div>
          {validation.resolved && validation.resolved !== value && (
            <div className="text-[10px] opacity-75 font-mono">↳ {validation.resolved}</div>
          )}
          {!validation.exists && (
            <button
              onClick={() => check(true)}
              disabled={checking}
              className="text-[11px] underline hover:opacity-80"
            >
              {t("settings.createThisFolder")}
            </button>
          )}
        </div>
      )}

      {field.hint && (
        <p className="text-[11px] text-muted flex items-start gap-1">
          <Info className="h-3 w-3 mt-0.5 flex-shrink-0" />
          <span>{trf(t, `settings.fields.${field.key}.hint`, field.hint)}</span>
        </p>
      )}
    </div>
  );
}

function FieldRow({ field, value, touched, revealed, onChange, onToggleReveal, helpText }: {
  field: SettingField;
  value: string;
  touched: boolean;
  revealed: boolean;
  onChange: (v: string) => void;
  onToggleReveal: () => void;
  /** #8: etiket yanına yardım balonu (? ikon) — bilinmeyen metrikler için. */
  helpText?: string;
}) {
  const t = useT();
  const showInput = !field.secret || revealed || touched;

  if (field.type === "bool") {
    const isOn = value === "true" || value === "1" || (typeof value === "boolean" && value);
    return (
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <label className="text-sm font-medium text-ink">{trf(t, `settings.fields.${field.key}.label`, field.label)}</label>
          <p className="text-[11px] text-muted font-mono">{field.key}</p>
        </div>
        <button
          onClick={() => onChange(isOn ? "false" : "true")}
          className={cn(
            "relative inline-flex h-6 w-11 items-center rounded-full transition",
            isOn ? "bg-brand-500" : "bg-bg-soft",
          )}
        >
          <span
            className={cn(
              "inline-block h-4 w-4 transform rounded-full bg-white transition shadow",
              isOn ? "translate-x-6" : "translate-x-1",
            )}
          />
        </button>
        {touched && <span className="text-[10px] text-warning">{t("settings.changed")}</span>}
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        {field.secret && <KeyRound className="h-3.5 w-3.5 text-muted" />}
        <div className="flex-1 flex items-center gap-1.5 min-w-0">
          <label className="text-sm font-medium text-ink truncate">{trf(t, `settings.fields.${field.key}.label`, field.label)}</label>
          {helpText && <InfoTip text={helpText} />}
        </div>
        <span className="text-[10px] text-muted font-mono">{field.key}</span>
        {field.is_set && !touched && (
          <span className="text-[10px] text-success bg-success-soft px-1.5 py-0.5 rounded-full">{t("settings.defined")}</span>
        )}
        {touched && (
          <span className="text-[10px] text-warning bg-warning-soft px-1.5 py-0.5 rounded-full">{t("settings.changed")}</span>
        )}
      </div>
      <div className="flex gap-2">
        <input
          type={field.secret && !revealed && !touched ? "password" : "text"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.default ?? (field.secret ? t("settings.apiKeyPlaceholder") : "")}
          className={cn(
            "flex-1 rounded-md border bg-white px-3 py-1.5 text-sm focus:outline-none focus:ring-2 transition",
            touched ? "border-brand-500 ring-brand-500/20" : "border-border focus:border-brand-500 focus:ring-brand-500/20",
            field.secret && "font-mono",
          )}
        />
        {field.secret && (
          <button
            type="button"
            onClick={onToggleReveal}
            className="p-1.5 rounded-md border border-border bg-white text-muted hover:text-ink hover:border-brand-300 transition"
            title={revealed ? t("common.hide") : t("common.show")}
          >
            {revealed ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
          </button>
        )}
      </div>
      {field.secret && field.is_set && !touched && (
        <p className="text-[10px] text-muted">{t("settings.maskedKeyNote")}</p>
      )}
    </div>
  );
}
