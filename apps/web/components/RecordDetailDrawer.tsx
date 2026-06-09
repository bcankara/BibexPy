"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  X, ExternalLink, Copy, Check, FileText, Users, Tag, Quote, BookOpen, Building2,
  Pencil, Save, Loader2,
} from "lucide-react";
import { api, translateApiError } from "@/lib/api-client";
import { useT } from "@/lib/i18n";
import { useToast } from "./Dialogs";
import { cn } from "@/lib/cn";

type Row = Record<string, string | null>;

type Props = {
  record: Row | null;
  projectId: string;
  onClose: () => void;
  onSaved?: () => void;
  startEditing?: boolean;
};

type FieldFormat = "wrap" | "compact" | "list" | "doi" | "long";
type FieldGroup = {
  title: string;
  icon: React.ReactNode;
  fields: { key: string; label: string; format?: FieldFormat }[];
};

function buildGroups(t: (k: string) => string): FieldGroup[] {
  return [
    {
      title: t("recordDetail.groups.citation"),
      icon: <BookOpen className="h-4 w-4" />,
      fields: [
        { key: "TI", label: t("recordDetail.fields.TI"), format: "wrap" },
        { key: "SO", label: t("recordDetail.fields.SO"), format: "wrap" },
        { key: "JI", label: t("recordDetail.fields.JI"), format: "compact" },
        { key: "PY", label: t("recordDetail.fields.PY") },
        { key: "VL", label: t("recordDetail.fields.VL") },
        { key: "IS", label: t("recordDetail.fields.IS") },
        { key: "BP", label: t("recordDetail.fields.BP") },
        { key: "EP", label: t("recordDetail.fields.EP") },
        { key: "PG", label: t("recordDetail.fields.PG") },
        { key: "DT", label: t("recordDetail.fields.DT") },
        { key: "LA", label: t("recordDetail.fields.LA") },
        { key: "DI", label: t("recordDetail.fields.DI"), format: "doi" },
        { key: "UT", label: t("recordDetail.fields.UT"), format: "compact" },
        { key: "PM", label: t("recordDetail.fields.PM"), format: "compact" },
        { key: "PU", label: t("recordDetail.fields.PU") },
        { key: "DB", label: t("recordDetail.fields.DB") },
      ],
    },
    {
      title: t("recordDetail.groups.authors"),
      icon: <Users className="h-4 w-4" />,
      fields: [
        { key: "AU", label: t("recordDetail.fields.AU"), format: "wrap" },
        { key: "AF", label: t("recordDetail.fields.AF"), format: "wrap" },
        { key: "C1", label: t("recordDetail.fields.C1"), format: "list" },
        { key: "RP", label: t("recordDetail.fields.RP"), format: "wrap" },
        { key: "EM", label: t("recordDetail.fields.EM"), format: "wrap" },
        { key: "OI", label: t("recordDetail.fields.OI"), format: "list" },
        { key: "RI", label: t("recordDetail.fields.RI"), format: "list" },
      ],
    },
    {
      title: t("recordDetail.groups.categories"),
      icon: <Tag className="h-4 w-4" />,
      fields: [
        { key: "DE", label: t("recordDetail.fields.DE"), format: "list" },
        { key: "ID", label: t("recordDetail.fields.ID"), format: "list" },
        { key: "WC", label: t("recordDetail.fields.WC"), format: "list" },
        { key: "SC", label: t("recordDetail.fields.SC"), format: "list" },
      ],
    },
    {
      title: t("recordDetail.groups.citations"),
      icon: <Quote className="h-4 w-4" />,
      fields: [
        { key: "TC", label: t("recordDetail.fields.TC") },
        { key: "Z9", label: t("recordDetail.fields.Z9") },
        { key: "U1", label: t("recordDetail.fields.U1") },
        { key: "U2", label: t("recordDetail.fields.U2") },
        { key: "NR", label: t("recordDetail.fields.NR") },
        { key: "CR", label: t("recordDetail.fields.CR"), format: "long" },
      ],
    },
    {
      title: t("recordDetail.groups.abstract"),
      icon: <FileText className="h-4 w-4" />,
      fields: [
        { key: "AB", label: t("recordDetail.fields.AB"), format: "long" },
      ],
    },
  ];
}

export function RecordDetailDrawer({ record, projectId, onClose, onSaved, startEditing = false }: Props) {
  const t = useT();
  const toast = useToast();
  const GROUPS = useMemo(() => buildGroups(t), [t]);
  const [mounted, setMounted] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  // Kaydedilen değişiklikleri kayıt değişene kadar yerelde tut — kaydettikten sonra
  // okuma görünümü ve yeniden düzenleme güncel değeri göstersin (prop tazelenmese de).
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [prevRecord, setPrevRecord] = useState(record);
  if (record !== prevRecord) { setPrevRecord(record); setOverrides({}); }

  // onClose'u ref'te tut — parent her render'da yeni closure verse de aşağıdaki
  // efektler yeniden çalışmasın (yoksa draft sıfırlanır = yazılan değer kaybolur).
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  // Açılış animasyonu + Esc + scroll kilidi — yalnız kayıt değişince çalışır
  useEffect(() => {
    if (!record) { setMounted(false); return; }
    requestAnimationFrame(() => setMounted(true));
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCloseRef.current(); };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [record]);

  // Draft başlatma yalnız kayıt veya başlangıç-modu değişince — düzenlerken sıfırlanmasın
  useEffect(() => {
    if (!record) return;
    if (startEditing) {
      const d: Record<string, string> = {};
      for (const k of Object.keys(record)) d[k] = record[k] == null ? "" : String(record[k]);
      setDraft(d);
      setEditing(true);
    } else {
      setEditing(false);
    }
  }, [record, startEditing]);

  if (!record) return null;

  // Görüntülenen kayıt = prop + kaydedilen yerel değişiklikler (overrides)
  const rec: Row = { ...record, ...overrides };

  function startEdit() {
    const d: Record<string, string> = {};
    for (const k of Object.keys(rec)) d[k] = rec[k] == null ? "" : String(rec[k]);
    setDraft(d);
    setEditing(true);
  }

  async function save() {
    setSaving(true);
    try {
      const changed: Record<string, string> = {};
      for (const k of Object.keys(draft)) {
        const orig = rec[k] == null ? "" : String(rec[k]);
        if (draft[k] !== orig) changed[k] = draft[k];
      }
      if (Object.keys(changed).length === 0) {
        setEditing(false);
        return;
      }
      await api.updateRecord(projectId, {
        uid: rec.UID || undefined,
        doi: rec.DI || undefined,
        fields: changed,
      });
      toast(t("recordDetail.saved", { n: Object.keys(changed).length }), { tone: "success" });
      setOverrides((o) => ({ ...o, ...changed }));   // okuma görünümü anında güncel değeri göstersin
      setEditing(false);
      onSaved?.();
    } catch (e) {
      toast(translateApiError(t, e, "recordDetail.saveFailed"), { tone: "danger" });
    } finally {
      setSaving(false);
    }
  }

  const title = rec.TI || `(${t("recordDetail.noTitle")})`;
  const authors = rec.AU || rec.AF || "";
  const year = rec.PY || "";
  const journal = rec.SO || rec.JI || "";
  const doi = rec.DI || "";

  const visibleGroups = GROUPS.map((g) => ({
    ...g,
    fields: g.fields.filter((f) => {
      const v = rec[f.key];
      return v != null && v !== "";
    }),
  })).filter((g) => g.fields.length > 0);

  const usedKeys = new Set(GROUPS.flatMap((g) => g.fields.map((f) => f.key)));
  const otherFields = Object.keys(rec)
    .filter((k) => !usedKeys.has(k) && rec[k] != null && rec[k] !== "")
    .sort();

  return (
    <>
      <div
        onClick={onClose}
        className={cn(
          "fixed inset-0 bg-ink/30 backdrop-blur-sm z-40 transition-opacity",
          mounted ? "opacity-100" : "opacity-0",
        )}
      />
      <div
        className={cn(
          "fixed top-0 right-0 h-full w-full max-w-2xl bg-white shadow-2xl z-50 flex flex-col",
          "transition-transform duration-200 ease-out",
          mounted ? "translate-x-0" : "translate-x-full",
        )}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-border flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 text-[11px] font-medium text-muted uppercase tracking-wide mb-1.5">
              <span>{t("recordDetail.title")}</span>
              {rec.DB && <span className="px-1.5 py-0.5 rounded bg-brand-50 text-brand-700">{rec.DB}</span>}
              {year && <span>· {year}</span>}
            </div>
            <h2 className="font-semibold text-base leading-snug text-ink">{title}</h2>
            {authors && <p className="text-xs text-muted mt-1 line-clamp-2">{authors}</p>}
            {journal && <p className="text-xs text-muted italic mt-0.5">{journal}</p>}
            {doi && !editing && (
              <a
                href={`https://doi.org/${doi}`}
                target="_blank" rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs text-brand-600 hover:underline mt-1.5 font-mono"
              >
                {doi}<ExternalLink className="h-3 w-3" />
              </a>
            )}
          </div>
          <div className="flex items-center gap-1.5 flex-shrink-0">
            {editing ? (
              <>
                <button
                  onClick={save}
                  disabled={saving}
                  className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-md bg-brand-500 hover:bg-brand-600 text-white disabled:opacity-50"
                >
                  {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                  {t("recordDetail.save")}
                </button>
                <button
                  onClick={() => setEditing(false)}
                  disabled={saving}
                  className="text-xs px-2.5 py-1.5 rounded-md text-muted hover:text-ink hover:bg-bg-soft"
                >
                  {t("common.cancel")}
                </button>
              </>
            ) : (
              <button
                onClick={startEdit}
                className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md border border-border text-muted hover:text-brand-700 hover:border-brand-400"
              >
                <Pencil className="h-3.5 w-3.5" /> {t("recordDetail.edit")}
              </button>
            )}
            <button
              onClick={onClose}
              className="p-1.5 rounded-md hover:bg-bg-soft text-muted hover:text-ink"
              title={`${t("common.close")} (Esc)`}
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {editing ? (
            <>
              <p className="text-[11px] text-muted">{t("recordDetail.editHint")}</p>
              {GROUPS.map((g) => (
                <section key={g.title}>
                  <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted mb-2">
                    <span className="text-brand-500">{g.icon}</span>{g.title}
                  </h3>
                  <div className="space-y-2">
                    {g.fields.map((f) => (
                      <EditableField
                        key={f.key} k={f.key} label={f.label} format={f.format}
                        value={draft[f.key] ?? ""}
                        onChange={(v) => setDraft((p) => ({ ...p, [f.key]: v }))}
                      />
                    ))}
                  </div>
                </section>
              ))}
              {otherFields.length > 0 && (
                <section>
                  <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted mb-2">
                    <Building2 className="h-4 w-4 text-brand-500" />{t("recordDetail.otherFields")}
                  </h3>
                  <div className="space-y-2">
                    {otherFields.map((k) => (
                      <EditableField
                        key={k} k={k} label={k} format="compact"
                        value={draft[k] ?? ""}
                        onChange={(v) => setDraft((p) => ({ ...p, [k]: v }))}
                      />
                    ))}
                  </div>
                </section>
              )}
            </>
          ) : (
            <>
              {visibleGroups.map((g) => (
                <section key={g.title}>
                  <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted mb-2">
                    <span className="text-brand-500">{g.icon}</span>{g.title}
                  </h3>
                  <dl className="space-y-1.5">
                    {g.fields.map((f) => (
                      <Field key={f.key} k={f.key} label={f.label} value={rec[f.key]!} format={f.format} />
                    ))}
                  </dl>
                </section>
              ))}
              {otherFields.length > 0 && (
                <section>
                  <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted mb-2">
                    <Building2 className="h-4 w-4 text-brand-500" />{t("recordDetail.otherFields")}
                  </h3>
                  <dl className="space-y-1.5">
                    {otherFields.map((k) => (
                      <Field key={k} k={k} label={k} value={rec[k]!} format="compact" />
                    ))}
                  </dl>
                </section>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

function EditableField({ k, label, value, format = "compact", onChange }: {
  k: string; label: string; value: string;
  format?: FieldFormat; onChange: (v: string) => void;
}) {
  const multiline = format === "long" || format === "wrap" || format === "list";
  return (
    <div className="grid grid-cols-[120px_1fr] gap-2 items-start text-xs">
      <label className="text-muted font-medium pt-1.5 truncate" title={`${label} (${k})`}>
        {label}<span className="ml-1 text-[10px] font-mono opacity-60">{k}</span>
      </label>
      {multiline ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={format === "long" ? 4 : 2}
          className="w-full rounded-md border border-border bg-white px-2 py-1 text-[12px] text-ink focus:outline-none focus:border-brand-500"
        />
      ) : (
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full rounded-md border border-border bg-white px-2 py-1 text-[12px] text-ink focus:outline-none focus:border-brand-500"
        />
      )}
    </div>
  );
}

function Field({ k, label, value, format = "compact" }: {
  k: string; label: string; value: string;
  format?: "wrap" | "compact" | "list" | "doi" | "long";
}) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {}
  }

  const items = format === "list" ? value.split(/[;|]\s*/).map((s) => s.trim()).filter(Boolean) : null;

  return (
    <div className="grid grid-cols-[120px_1fr_auto] gap-2 items-start group text-xs">
      <dt className="text-muted font-medium pt-0.5 truncate" title={`${label} (${k})`}>
        <span>{label}</span>
        <span className="ml-1 text-[10px] font-mono opacity-60">{k}</span>
      </dt>
      <dd className="min-w-0">
        {format === "doi" ? (
          <a href={`https://doi.org/${value}`} target="_blank" rel="noreferrer" className="text-brand-600 hover:underline font-mono break-all">
            {value}
          </a>
        ) : format === "list" && items ? (
          <div className="flex flex-wrap gap-1">
            {items.slice(0, 30).map((it, i) => (
              <span key={i} className="px-1.5 py-0.5 rounded bg-bg-soft border border-border text-[11px]">{it}</span>
            ))}
            {items.length > 30 && <span className="text-[11px] text-muted">+{items.length - 30}</span>}
          </div>
        ) : format === "long" ? (
          <p className="text-ink whitespace-pre-wrap leading-relaxed text-[12px] max-h-56 overflow-y-auto pr-2">{value}</p>
        ) : format === "wrap" ? (
          <p className="text-ink break-words">{value}</p>
        ) : (
          <p className="text-ink truncate" title={value}>{value}</p>
        )}
      </dd>
      <button
        onClick={copy}
        className="opacity-0 group-hover:opacity-100 transition p-1 rounded hover:bg-bg-soft text-muted"
        title={t("common.copy")}
      >
        {copied ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
      </button>
    </div>
  );
}
