"use client";

import Link from "next/link";
import { useState } from "react";
import {
  Check,
  Copy,
  ExternalLink,
  FolderKanban,
  Github,
  Globe,
  Instagram,
  LifeBuoy,
  Mail,
  Quote,
  Settings as SettingsIcon,
  Sparkles,
  Twitter,
  Wrench,
  Youtube,
} from "lucide-react";
import { ImageWithFallback } from "@/components/ImageWithFallback";
import { LocaleFlag } from "@/components/AppShell";
import { cn } from "@/lib/cn";
import { useI18n, useT, type Locale } from "@/lib/i18n";

const HELP_URL = "https://bibexpy.com/doc/";

const BIBTEX = `@article{bibexpy2025,
  title     = {BibexPy: Harmonizing the bibliometric symphony of {Scopus} and {Web of Science}},
  author    = {Kara, Burak Can and {\\c{S}}ahin, Alperen and Dirsehan, Ta{\\c{s}}k{\\i}n},
  journal   = {SoftwareX},
  volume    = {30},
  pages     = {102098},
  year      = {2025},
  publisher = {Elsevier},
  doi       = {10.1016/j.softx.2025.102098}
}`;

const SOCIAL = [
  { href: "https://github.com/bcankara/BibexPy", icon: <Github className="h-4 w-4" />, label: "GitHub" },
  { href: "https://www.youtube.com/@BibexPy", icon: <Youtube className="h-4 w-4" />, label: "YouTube" },
  { href: "https://twitter.com/BibexPy", icon: <Twitter className="h-4 w-4" />, label: "X (Twitter)" },
  { href: "https://www.instagram.com/bibexpy/", icon: <Instagram className="h-4 w-4" />, label: "Instagram" },
  { href: "https://bibexpy.com", icon: <Globe className="h-4 w-4" />, label: "bibexpy.com" },
  { href: "mailto:info@bibexpy.com", icon: <Mail className="h-4 w-4" />, label: "info@bibexpy.com" },
];

export default function Home() {
  const { tArr, locale, setLocale } = useI18n();
  const authorsList = tArr("home.authors");

  return (
    <div className="relative flex min-h-[100svh] w-full flex-col justify-center overflow-hidden bg-[#f7f9fc] text-ink">
      {/* Atmosfer — ince grid + cyan hale (radial mask ile merkeze odaklı) */}
      <div className="pointer-events-none absolute inset-0 opacity-[0.5] [background-image:linear-gradient(to_right,#dbe3ee_1px,transparent_1px),linear-gradient(to_bottom,#dbe3ee_1px,transparent_1px)] [background-size:42px_42px] [mask-image:radial-gradient(ellipse_at_center,black_25%,transparent_72%)]" />
      <div className="pointer-events-none absolute left-1/2 top-10 h-72 w-72 -translate-x-1/2 rounded-full bg-cyan-300/25 blur-3xl" />

      {/* Helium — v2 kod adı; arka plana köşelere/yanlara serpiştirilmiş ÇOK soluk
          "Helium"/"He" filigranları (merkezde değil, dikkat dağıtmaz). */}
      <div aria-hidden className="pointer-events-none absolute inset-0 z-0 select-none overflow-hidden">
        <span className="absolute left-[3%] top-[10%] -rotate-12 text-7xl font-black text-violet-900/[0.045]">Helium</span>
        <span className="absolute right-[4%] top-[18%] rotate-6 text-8xl font-black text-violet-900/[0.04]">He</span>
        <span className="absolute left-[7%] bottom-[12%] rotate-3 text-6xl font-black text-violet-900/[0.05]">He</span>
        <span className="absolute right-[5%] bottom-[9%] -rotate-6 text-7xl font-black text-violet-900/[0.04]">Helium</span>
        <span className="absolute left-[26%] top-[3%] text-5xl font-black text-violet-900/[0.035]">He</span>
        <span className="absolute right-[24%] bottom-[3%] text-5xl font-black text-violet-900/[0.035]">Helium</span>
      </div>

      {/* Dil seçici — sayfanın sağ üst köşesine yedirilmiş kompakt bayrak toggle (ayrı header yok) */}
      <div className="absolute right-4 top-4 z-20 sm:right-6 sm:top-6">
        <div className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-white/85 p-1 shadow-[0_8px_24px_-16px_rgba(15,23,42,0.5)] backdrop-blur">
          {(["en", "tr"] as Locale[]).map((l) => (
            <button
              key={l}
              type="button"
              onClick={() => setLocale(l)}
              aria-label={l === "en" ? "English" : "Türkçe"}
              title={l === "en" ? "English" : "Türkçe"}
              className={cn(
                "flex h-7 w-9 items-center justify-center overflow-hidden rounded-full ring-1 transition",
                locale === l ? "ring-cyan-500 shadow-[0_4px_12px_-6px_rgba(12,40,71,0.6)]" : "ring-transparent opacity-55 hover:opacity-100",
              )}
            >
              <LocaleFlag code={l} />
            </button>
          ))}
        </div>
      </div>

      <div className="relative z-10 mx-auto w-full max-w-5xl px-6 py-8">
        <Launcher />
        <CitationAndAuthors authors={authorsList} />
      </div>
    </div>
  );
}

/* ─────────────────────────── Launcher: logo + centered menu ─────────────────────────── */

function Launcher() {
  const t = useT();

  const menu: MenuItem[] = [
    { href: "/projects", icon: <FolderKanban className="h-6 w-6" />, label: t("nav.projects"), primary: true },
    { href: "/tools", icon: <Wrench className="h-6 w-6" />, label: t("nav.tools"), primary: true },
    { href: "/settings", icon: <SettingsIcon className="h-6 w-6" />, label: t("nav.settings") },
    { href: HELP_URL, icon: <LifeBuoy className="h-6 w-6" />, label: t("nav.help"), external: true },
  ];

  return (
    <section className="flex flex-col items-center text-center">
      {/* Tam marka lockup'ı tek görselde — atom + BIBEXPY + "V2.0.0 Helium"
          rozeti + slogan (kaynak 3120×900, küçültünce keskin). */}
      <img
        src="/images/bibexpy-logo-full.png"
        alt="BibexPy — V2.0.0 Helium — Bibliometrics Experience with Python"
        draggable={false}
        className="h-auto w-[clamp(280px,40vw,440px)] select-none drop-shadow-[0_20px_50px_rgba(12,40,71,0.16)]"
      />

      <h1 className="mt-5 max-w-xl text-balance text-xl font-black leading-tight tracking-tight text-slate-950 sm:text-2xl">
        {t("home.heroTitle")}
      </h1>

      {/* Ortada kare menü */}
      <nav className="mt-7 grid w-full max-w-2xl grid-cols-2 gap-3 sm:grid-cols-4">
        {menu.map((m) => (
          <MenuTile key={m.label} {...m} />
        ))}
      </nav>
    </section>
  );
}

type MenuItem = {
  href: string;
  icon: React.ReactNode;
  label: string;
  external?: boolean;
  primary?: boolean;
};

function MenuTile({ href, icon, label, external, primary }: MenuItem) {
  const inner = (
    <div
      className={cn(
        "group relative flex aspect-square flex-col items-center justify-center gap-2.5 rounded-[1.3rem] border bg-white p-3 text-center",
        "shadow-[0_18px_50px_-40px_rgba(15,23,42,0.6)] transition duration-200",
        "hover:-translate-y-1.5 hover:shadow-[0_28px_60px_-32px_rgba(12,40,71,0.5)]",
        primary ? "border-cyan-200 hover:border-cyan-400" : "border-slate-200 hover:border-cyan-300",
      )}
    >
      <span
        className={cn(
          "flex h-12 w-12 items-center justify-center rounded-2xl transition",
          primary
            ? "bg-cyan-600 text-white shadow-[0_12px_28px_-14px_rgba(12,40,71,0.7)] group-hover:bg-cyan-500"
            : "bg-[#0c2847] text-cyan-200 group-hover:bg-[#173553] group-hover:text-cyan-100",
        )}
      >
        {icon}
      </span>
      <span className="inline-flex items-center gap-1 text-[13px] font-black text-slate-800 group-hover:text-slate-950">
        {label}
        {external && <ExternalLink className="h-3 w-3 text-slate-400" />}
      </span>
    </div>
  );

  return external ? (
    <a href={href} target="_blank" rel="noreferrer">{inner}</a>
  ) : (
    <Link href={href}>{inner}</Link>
  );
}

/* ─────────────────────────── Authors + Cite (+ social) band ─────────────────────────── */

function CitationAndAuthors({ authors }: { authors: string[] }) {
  const t = useT();
  const [copied, setCopied] = useState(false);

  async function copyBibtex() {
    try {
      await navigator.clipboard.writeText(BIBTEX);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {}
  }

  return (
    <section className="relative z-10 mt-8">
      <div className="overflow-hidden rounded-[1.7rem] bg-white shadow-[0_44px_120px_-60px_rgba(12,40,71,0.5)] ring-1 ring-slate-900/10">
        <div className="grid lg:grid-cols-[1.55fr_1fr]">
          {/* Yazarlar — beyaz panel */}
          <div className="p-5 md:p-6">
            <div className="mb-4 inline-flex h-7 items-center gap-1.5 rounded-full bg-cyan-50 px-3 text-[11px] font-black uppercase tracking-[0.12em] text-cyan-700 ring-1 ring-cyan-200/70">
              <Sparkles className="h-3.5 w-3.5" /> {t("home.authorsTitle")}
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              {authors.map((line) => {
                const [name, affiliation, orcid, photoSlug] = line.split("|");
                return <AuthorCard key={name} name={name} affiliation={affiliation} orcid={orcid} photoSlug={photoSlug} />;
              })}
            </div>
          </div>

          {/* Cite + bağlantılar — koyu panel (#0c2847), markaya yedirilmiş */}
          <div className="relative flex flex-col overflow-hidden bg-[#0c2847] p-5 text-white md:p-6">
            <div className="pointer-events-none absolute -right-16 -top-16 h-44 w-44 rounded-full bg-cyan-500/25 blur-3xl" />
            <div className="pointer-events-none absolute inset-0 opacity-[0.22] [background-image:linear-gradient(to_right,#202a4a_1px,transparent_1px),linear-gradient(to_bottom,#202a4a_1px,transparent_1px)] [background-size:28px_28px]" />
            <div className="relative flex h-full flex-col">
              <div className="mb-2.5 flex items-center gap-2.5">
                <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-cyan-500/15 text-cyan-200 ring-1 ring-cyan-300/30">
                  <Quote className="h-4 w-4" />
                </span>
                <h2 className="text-base font-black tracking-tight">{t("home.citationTitle")}</h2>
              </div>
              <blockquote className="rounded-2xl border border-white/10 bg-white/[0.05] p-3 text-[12px] leading-6 text-white/75">
                {t("home.citationApa")}
              </blockquote>
              <div className="mt-3 flex flex-wrap gap-2">
                <a
                  href="https://doi.org/10.1016/j.softx.2025.102098"
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-2 rounded-xl bg-teal-600 px-3 py-1.5 text-xs font-black text-white transition hover:-translate-y-0.5 hover:bg-teal-500"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  {t("home.citationOpenPaper")}
                </a>
                <button
                  onClick={copyBibtex}
                  className="inline-flex items-center gap-2 rounded-xl border border-violet-400/40 bg-violet-900 px-3 py-1.5 text-xs font-bold text-white transition hover:border-violet-300/60 hover:bg-violet-950"
                >
                  {copied ? <Check className="h-3.5 w-3.5 text-teal-300" /> : <Copy className="h-3.5 w-3.5" />}
                  {copied ? t("home.citationCopied") : t("home.citationCopyBib")}
                </button>
              </div>

              {/* Önemli adresler — cite altındaki boşluğu dolduran sosyal/link şeridi */}
              <div className="mt-auto pt-4">
                <div className="flex flex-wrap items-center gap-2 border-t border-white/10 pt-3">
                  {SOCIAL.map((s) => (
                    <a
                      key={s.label}
                      href={s.href}
                      target="_blank"
                      rel="noreferrer"
                      title={s.label}
                      aria-label={s.label}
                      className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 bg-white/[0.06] text-white/70 transition hover:-translate-y-0.5 hover:border-cyan-300/40 hover:bg-cyan-500/15 hover:text-cyan-200"
                    >
                      {s.icon}
                    </a>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function AuthorCard({ name, affiliation, orcid, photoSlug }: {
  name: string;
  affiliation: string;
  orcid?: string;
  photoSlug?: string;
}) {
  const initials = name
    .replace(/,?\s*ph\.?d\.?$/i, "")
    .trim()
    .split(/\s+/)
    .map((word) => word[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  const orcidUrl = orcid ? `https://orcid.org/${orcid}` : undefined;

  return (
    <article className="flex h-full flex-col rounded-2xl border border-slate-200 bg-slate-50/70 p-3 text-center transition hover:border-cyan-300/60 hover:bg-white hover:shadow-[0_18px_44px_-30px_rgba(12,40,71,0.5)]">
      <div className="flex flex-1 flex-col">
        <div className="mx-auto mb-2.5 h-16 w-16 overflow-hidden rounded-2xl bg-[#0c2847] shadow-sm ring-1 ring-slate-900/10">
          {photoSlug ? (
            <ImageWithFallback
              src={`/images/authors/${photoSlug}.jpg`}
              alt={name}
              className="h-full w-full object-cover"
              fallback={<div className="flex h-full w-full items-center justify-center text-lg font-black text-white">{initials}</div>}
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-lg font-black text-white">{initials}</div>
          )}
        </div>
        <h3 className="text-[13px] font-black leading-tight text-slate-950">{name}</h3>
        <p className="mt-1 text-[10.5px] leading-4 text-slate-500">{affiliation}</p>
      </div>
      {orcidUrl && (
        <a
          href={orcidUrl}
          target="_blank"
          rel="noreferrer"
          className="mt-3 inline-flex min-h-9 w-full items-center justify-center gap-1.5 rounded-xl border border-slate-200 bg-white px-2 py-1.5 font-mono text-[9px] font-bold leading-none text-cyan-700 transition hover:border-cyan-300 hover:text-cyan-900"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          <span className="whitespace-nowrap">ORCID {orcid}</span>
        </a>
      )}
    </article>
  );
}
