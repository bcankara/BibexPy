"use client";
import { useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { DisambiguatePanel } from "@/components/DisambiguatePanel";
import { QualityDashboard } from "@/components/QualityDashboard";
import { StepNav } from "@/components/StepNav";
import { AuditLogPanel } from "@/components/AuditLogPanel";
import {
  Brain, Users, Building2, Tag, Newspaper, Layers, BookOpenCheck,
  Clock, ArrowRight, ChevronDown, ChevronUp, Globe, Landmark,
} from "lucide-react";
import { useT, useI18n } from "@/lib/i18n";
import { cn } from "@/lib/cn";
import { useProjectId } from "@/lib/use-project-id";

/**
 * AI Asistan sayfası — bibliometrik veri hazırlık için LLM tabanlı işler.
 *
 * Akış:
 * - Hazırlık & Filtre (Adım 3) eksik alanları API'lerden tamamlar (deterministik)
 * - AI Asistan (Adım 4) ise SEMANTİK karar gereken yerlerde devreye girer:
 *     • Disambiguation (yazar / affiliation varyantlarını birleştirme)
 *     • Anahtar kelime / dergi adı / kategori normalizasyonu
 *     • Akıllı semantik duplicate tespiti
 */

type ToolStatus = "live" | "coming_soon";

type AITool = {
  id: string;
  fields: string;       // hangi alanlara dokunur (kod, çevirilmiyor)
  icon: React.ReactNode;
  status: ToolStatus;
  category: "disambiguation" | "normalization" | "extraction";
};

const TOOLS: AITool[] = [
  { id: "authors",      fields: "AU",          icon: <Users className="h-5 w-5" />,         status: "live",         category: "disambiguation" },
  { id: "affiliations", fields: "C1, C3",      icon: <Building2 className="h-5 w-5" />,     status: "live",         category: "disambiguation" },
  { id: "organizations",fields: "C1 → üst kurum", icon: <Landmark className="h-5 w-5" />,    status: "live",         category: "normalization" },
  { id: "countries",    fields: "C1 (ülke)",   icon: <Globe className="h-5 w-5" />,          status: "live",         category: "normalization" },
  { id: "keywords",     fields: "DE, ID",      icon: <Tag className="h-5 w-5" />,           status: "coming_soon",  category: "normalization" },
  { id: "journals",     fields: "SO, JI",      icon: <Newspaper className="h-5 w-5" />,     status: "coming_soon",  category: "normalization" },
  { id: "categories",   fields: "WC, SC ← AB", icon: <Layers className="h-5 w-5" />,        status: "coming_soon",  category: "extraction" },
  { id: "kw-from-ab",   fields: "DE ← AB",     icon: <BookOpenCheck className="h-5 w-5" />, status: "coming_soon",  category: "extraction" },
];

export default function EnrichPage() {
  const id = useProjectId();
  const t = useT();
  const [activeTool, setActiveTool] = useState<string>("authors");
  const [showAudit, setShowAudit] = useState(false);

  const byCat = (c: string) => TOOLS.filter((tool) => tool.category === c);

  return (
    <>
      <PageHeader
        title={t("enrich.title")}
        subtitle={t("enrich.subtitle")}
        badges={[{ label: t("enrich.stepBadge"), tone: "neutral" }]}
        right={
          <StepNav
            onHistory={() => setShowAudit(true)}
            nextHref={`/projects/${id}/export`}
            nextLabel={t("nav.export")}
          />
        }
      />

      <div className="max-w-6xl mx-auto px-6 py-6 space-y-4">

        {/* Bilgilendirme — bu sayfa Hazırlık'tan ne farklı? */}
        <Card>
          <CardBody className="py-3">
            <div className="flex items-start gap-3 text-xs">
              <div className="w-8 h-8 rounded-lg bg-brand-50 text-brand-600 flex items-center justify-center flex-shrink-0">
                <Brain className="h-4 w-4" />
              </div>
              <div className="flex-1 space-y-1">
                <p className="text-ink" dangerouslySetInnerHTML={{ __html: t("enrich.infoBanner1") }} />
                <p className="text-ink" dangerouslySetInnerHTML={{ __html: t("enrich.infoBanner2") }} />
              </div>
            </div>
          </CardBody>
        </Card>

        {/* Veri Kalitesi & Doldurma — filtreden sonra, yalnız kalan kayıtlar üzerinde.
            Kayıt tablosu bu sayfada yok; onFieldClick geçilmez. */}
        <QualityDashboard projectId={id} onChanged={() => {}} />

        {/* Araç seçici (sol grid + sağ panel) */}
        <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-4">
          {/* Sol: araç listesi */}
          <aside className="space-y-4">
            <ToolCategorySection category="disambiguation" tools={byCat("disambiguation")} activeTool={activeTool} onSelect={setActiveTool} />
            <ToolCategorySection category="normalization" tools={byCat("normalization")} activeTool={activeTool} onSelect={setActiveTool} />
            <ToolCategorySection category="extraction" tools={byCat("extraction")} activeTool={activeTool} onSelect={setActiveTool} />
          </aside>

          {/* Sağ: aktif aracın paneli */}
          <section className="min-w-0">
            {["authors", "affiliations", "countries", "organizations"].includes(activeTool) ? (
              <DisambiguatePanel projectId={id} kind={activeTool as "authors" | "affiliations" | "countries" | "organizations"} />
            ) : (
              <ComingSoonPanel tool={TOOLS.find((t) => t.id === activeTool)!} />
            )}
          </section>
        </div>
      </div>
      <AuditLogPanel projectId={id} open={showAudit} onClose={() => setShowAudit(false)} />
    </>
  );
}

function ToolCategorySection({ category, tools, activeTool, onSelect }: {
  category: string;
  tools: AITool[];
  activeTool: string;
  onSelect: (id: string) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(true);
  if (tools.length === 0) return null;
  return (
    <div className="space-y-2">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-1 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted hover:text-ink"
      >
        <span>{t(`enrich.categories.${category}.label`)}</span>
        {open ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
      </button>
      {open && (
        <div className="space-y-1.5">
          {tools.map((tool) => (
            <ToolListItem
              key={tool.id}
              tool={tool}
              active={activeTool === tool.id}
              onClick={() => onSelect(tool.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ToolListItem({ tool, active, onClick }: { tool: AITool; active: boolean; onClick: () => void }) {
  const t = useT();
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full text-left rounded-lg border px-3 py-2.5 transition group",
        active
          ? "border-brand-500 bg-brand-50 ring-2 ring-brand-500/20"
          : "border-border bg-white hover:border-brand-300",
      )}
    >
      <div className="flex items-start gap-2.5">
        <div
          className={cn(
            "w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0",
            active ? "bg-brand-500 text-white" : "bg-brand-50 text-brand-600",
          )}
        >
          {tool.icon}
        </div>
        <div className="flex-1 min-w-0">
          <span className={cn("text-sm font-semibold truncate block", active ? "text-brand-700" : "text-ink")}>
            {t(`enrich.tools.${tool.id}.title`)}
          </span>
          <p className="text-[11px] text-muted truncate mt-0.5">
            {tool.fields}
          </p>
        </div>
        {active && <ArrowRight className="h-3.5 w-3.5 text-brand-500 mt-1" />}
      </div>
    </button>
  );
}

function ComingSoonPanel({ tool }: { tool: AITool }) {
  const t = useT();
  const { tArr } = useI18n();
  const steps = tArr(`enrich.howSteps`);
  return (
    <Card>
      <CardHeader>
        <span className="w-9 h-9 rounded-lg bg-warning-soft text-warning flex items-center justify-center">
          {tool.icon}
        </span>
        <div className="flex-1">
          <h2 className="font-semibold text-base">{t(`enrich.tools.${tool.id}.title`)}</h2>
          <p className="text-xs text-muted">{t("enrich.affectedFields")}: <span className="font-mono">{tool.fields}</span></p>
        </div>
        <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-warning-soft text-warning border border-warning/30">
          <Clock className="h-3 w-3 inline mr-1" /> {t("enrich.comingSoon")}
        </span>
      </CardHeader>
      <CardBody className="space-y-5">
        <div className="rounded-lg border border-border bg-bg-soft/40 px-4 py-3 text-sm text-ink leading-relaxed">
          {t(`enrich.tools.${tool.id}.description`)}
        </div>

        <div>
          <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted mb-2">
            {t("enrich.howWillItWork")}
          </h3>
          <ol className="space-y-2 text-sm text-ink">
            {steps.map((s, i) => (
              <Step key={i} n={i + 1}>{s}</Step>
            ))}
          </ol>
        </div>

        <div className="rounded-lg border border-info/30 bg-info-soft/50 px-4 py-3 text-xs text-blue-900">
          <strong>{t("enrich.devStatusLabel")}:</strong> {t("enrich.devStatusBody")}
        </div>
      </CardBody>
    </Card>
  );
}

function Step({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="w-5 h-5 rounded-full bg-brand-100 text-brand-700 text-[10px] font-bold flex items-center justify-center flex-shrink-0 mt-0.5">
        {n}
      </span>
      <span>{children}</span>
    </li>
  );
}
