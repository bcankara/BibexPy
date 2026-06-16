"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BookOpen, Code2, ExternalLink, FileText, FolderKanban, Github, Globe, Home,
  Instagram, LifeBuoy, Mail, Settings as SettingsIcon, Twitter, Wrench, Youtube,
} from "lucide-react";
import { useI18n, type Locale } from "@/lib/i18n";
import { cn } from "@/lib/cn";
import { api, DOCS_URL } from "@/lib/api-client";

/**
 * AppShell — AutoCAD/Office ribbon paterni:
 *  • Solda kompakt logo bloğu (yalnız marka) — anasayfada gizlenir çünkü
 *    Hero zaten büyük marka kimliğini taşır; iç sayfalarda görünür kalır.
 *  • Sağda ribbon paneller — 4 grup (Workspaces / System / Resources / Language)
 *    Her panel: butonlar üstte, panel başlığı altta (uppercase, small caps)
 *    Paneller arası ince dikey ayırıcı
 *  • Language paneli en sağda (ml-auto), boş kalan alanı doldurur
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const { locale, setLocale, t } = useI18n();
  const pathname = usePathname();
  const isHome = pathname === "/";

  // DOCS_URL runtime'da window.location origin'ine göre çözülür; SSR/build
  // sırasında ise relative "/docs" olur. Hydration uyuşmazlığını önlemek için
  // ilk render'da "/docs" (SSR ile aynı), mount'tan sonra gerçek DOCS_URL.
  const [docsHref, setDocsHref] = useState("/docs");
  useEffect(() => { setDocsHref(DOCS_URL); }, []);

  // Aktif sürüm — footer'da "hangi sürüm çalışıyor" göstermek için /api/health'ten.
  const [appVersion, setAppVersion] = useState<{ version: string; codename: string } | null>(null);
  useEffect(() => {
    let alive = true;
    api.health()
      .then((h) => { if (alive) setAppVersion({ version: h.version, codename: h.codename }); })
      .catch(() => { /* health erişilemezse sürüm gösterilmez */ });
    return () => { alive = false; };
  }, []);

  return (
    <>
      {/* Header sayfa background'ı ile aynı (#f7f9fc) — ayrı bir "bar" gibi
          değil sayfanın doğal başlangıcı gibi görünür. Card'lar (logo, ribbon)
          beyaz arka planda yüzer. */}
      {!isHome && (
      <header className="border-b border-slate-300/70 bg-[#f7f9fc] text-slate-900 shadow-[0_1px_0_rgba(255,255,255,0.04)]">
        <div className="mx-auto flex max-w-7xl flex-wrap items-stretch gap-3 px-4 py-3 sm:px-6">
          {/* === Ribbon (logo + paneller tek beyaz container içinde) === */}
          {/* Paneller içerik genişliğinde; ribbon `justify-between` ile onları
              eşit boşluklarla dağıtır — Logo solda, Language sağda,
              arada Workspaces/System/Resources dengeli yayılır. */}
          <div className={cn(
            "flex min-w-0 items-stretch overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_4px_14px_-12px_rgba(15,23,42,0.25)]",
            // Anasayfada nav panelleri gövdede ortada (Launcher) — header'da yalnız
            // dil seçici kalır, sağa yaslı. İç sayfalarda tam ribbon.
            isHome ? "ml-auto" : "flex-1 justify-between",
          )}>
            {!isHome && (
              <>
                {/* Logo — container içinde, container yüksekliğini dolduran kare
                    lockup; sağındaki dikey çizgi (border-r) menüden ayırır. */}
                <Link
                  href="/"
                  aria-label="BibexPy — ana sayfa"
                  className="group flex shrink-0 items-center border-r border-slate-200 px-4 py-2 transition hover:bg-slate-50/70"
                >
                  <img
                    src="/images/bibexpy-logo-header.png"
                    alt="BibexPy — V2.0.0 Helium"
                    draggable={false}
                    className="h-full max-h-32 w-auto select-none transition group-hover:opacity-90"
                  />
                </Link>

                <RibbonPanel title={t("nav.panel.workspaces")}>
                  <RibbonButton
                    href="/"
                    icon={<Home className="h-7 w-7" />}
                    label={t("nav.home")}
                  />
                  <RibbonButton
                    href="/projects"
                    icon={<FolderKanban className="h-7 w-7" />}
                    label={t("nav.projects")}
                  />
                  <RibbonButton
                    href="/tools"
                    icon={<Wrench className="h-7 w-7" />}
                    label={t("nav.tools")}
                  />
                </RibbonPanel>

                <RibbonPanel title={t("nav.panel.system")}>
                  <RibbonButton
                    href="/settings"
                    icon={<SettingsIcon className="h-7 w-7" />}
                    label={t("nav.settings")}
                  />
                </RibbonPanel>

                <RibbonPanel title={t("nav.panel.resources")}>
                  <RibbonButton
                    href={docsHref}
                    icon={<Code2 className="h-7 w-7" />}
                    label="API"
                    external
                  />
                  <RibbonButton
                    href="https://github.com/bcankara/BibexPy"
                    icon={<BookOpen className="h-7 w-7" />}
                    label={t("nav.source")}
                    external
                  />
                </RibbonPanel>
              </>
            )}

            {/* Language panel — gerçek bayrak ikonları + her dilin kendi adı.
                Dil her zaman kendi yerel adıyla yazılır (English / Türkçe),
                Wikipedia ve çoğu çok-dilli sitenin standardı.
                `justify-between` ribbon container'da sağa dağıtır, ml-auto gereksiz. */}
            <RibbonPanel title={t("nav.panel.locale")}>
              {(["en", "tr"] as Locale[]).map((l) => (
                <RibbonButton
                  key={l}
                  onClick={() => setLocale(l)}
                  icon={<LocaleFlag code={l} />}
                  label={l === "en" ? "English" : "Türkçe"}
                  active={locale === l}
                  bareIcon
                />
              ))}
            </RibbonPanel>
          </div>
        </div>
      </header>
      )}

      <main className="flex-1">{children}</main>

      {/* Footer — anasayfada gizli (Launcher kendi linklerini/cite'ını taşır).
          Profesyonel 3-bölge: marka+açıklama+sosyal / Kaynaklar / Proje + alt bar. */}
      {!isHome && (
      <footer className="relative border-t border-slate-200 bg-gradient-to-b from-white to-[#eef2f8]">
        {/* ince cyan üst aksan çizgisi */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-cyan-400/60 to-transparent"
        />
        <div className="mx-auto max-w-6xl px-6 py-10">
          <div className="grid grid-cols-2 gap-x-8 gap-y-9 md:grid-cols-[1.7fr_1fr_1fr]">
            {/* Marka + açıklama + sosyal */}
            <div className="col-span-2 md:col-span-1">
              {/* Logo, alttaki açıklama bloğuyla aynı genişlikte (max-w-xs) ve
                  sol-hizalı — sağ kenarları da hizalanır. */}
              <Link
                href="/"
                aria-label="BibexPy"
                className="block w-full max-w-xs transition hover:opacity-90"
              >
                <img
                  src="/images/bibexpy-logo-in.png"
                  alt="BibexPy — V2.0.0 Helium — Bibliometrics Experience with Python"
                  draggable={false}
                  className="h-auto w-full select-none"
                />
              </Link>
              <p className="mt-4 max-w-xs text-xs leading-relaxed text-slate-500">
                {t("common.footerTagline")}
              </p>
              <div className="mt-4 flex items-center gap-1.5">
                <FooterIcon href="https://github.com/bcankara/BibexPy" label="GitHub">
                  <Github className="h-4 w-4" />
                </FooterIcon>
                <FooterIcon href="https://www.youtube.com/@BibexPy" label="YouTube">
                  <Youtube className="h-4 w-4" />
                </FooterIcon>
                <FooterIcon href="https://twitter.com/BibexPy" label="X (Twitter)">
                  <Twitter className="h-4 w-4" />
                </FooterIcon>
                <FooterIcon href="https://www.instagram.com/bibexpy/" label="Instagram">
                  <Instagram className="h-4 w-4" />
                </FooterIcon>
              </div>
            </div>

            {/* Kaynaklar */}
            <FooterCol title={t("common.footerResources")}>
              <FooterLink href={docsHref} external icon={<LifeBuoy className="h-3.5 w-3.5" />}>
                {t("common.footerDocs")}
              </FooterLink>
              <FooterLink href="/tools" icon={<Wrench className="h-3.5 w-3.5" />}>
                {t("nav.tools")}
              </FooterLink>
              <FooterLink href="/settings" icon={<SettingsIcon className="h-3.5 w-3.5" />}>
                {t("nav.settings")}
              </FooterLink>
            </FooterCol>

            {/* Proje */}
            <FooterCol title={t("common.footerProject")}>
              <FooterLink
                href="https://doi.org/10.1016/j.softx.2025.102098"
                external
                icon={<FileText className="h-3.5 w-3.5" />}
              >
                {t("common.footerPaper")}
              </FooterLink>
              <FooterLink href="https://bibexpy.com" external icon={<Globe className="h-3.5 w-3.5" />}>
                {t("common.footerWebsite")}
              </FooterLink>
              <FooterLink href="mailto:info@bibexpy.com" external icon={<Mail className="h-3.5 w-3.5" />}>
                {t("common.footerContact")}
              </FooterLink>
            </FooterCol>
          </div>

          {/* Alt bar — telif + lisans + atıf */}
          <div className="mt-9 flex flex-col items-start justify-between gap-3 border-t border-slate-200/70 pt-5 text-[11px] text-slate-400 sm:flex-row sm:items-center">
            <span>
              © 2025 BibexPy ·{" "}
              <a
                href="https://www.gnu.org/licenses/gpl-3.0"
                target="_blank"
                rel="noreferrer"
                className="font-medium text-slate-500 transition hover:text-cyan-700"
              >
                GPL-3.0-or-later
              </a>{" "}
              · {t("common.footerRights")}
              {appVersion && (
                <>
                  {" · "}
                  <span className="font-semibold text-slate-500" title={t("common.footerVersionTitle")}>
                    {appVersion.version === "dev"
                      ? `dev · ${appVersion.codename}`
                      : `v${appVersion.version} · ${appVersion.codename}`}
                  </span>
                </>
              )}
            </span>
            <a
              href="https://doi.org/10.1016/j.softx.2025.102098"
              target="_blank"
              rel="noreferrer"
              className="group inline-flex items-center gap-1.5 font-semibold text-slate-500 transition hover:text-cyan-700"
            >
              {t("common.footerCitation")}
              <span className="inline-block text-cyan-600 transition group-hover:translate-x-0.5">
                {t("common.footerCite")}
              </span>
            </a>
          </div>
        </div>
      </footer>
      )}
    </>
  );
}

/* ───────────── Footer primitives ───────────── */

function FooterCol({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-[11px] font-bold uppercase tracking-[0.14em] text-slate-400">{title}</h3>
      <ul className="mt-3.5 space-y-2.5">{children}</ul>
    </div>
  );
}

function FooterLink({
  href,
  external = false,
  icon,
  children,
}: {
  href: string;
  external?: boolean;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  const cls =
    "group inline-flex items-center gap-2 text-xs font-medium text-slate-500 transition hover:text-cyan-700";
  const inner = (
    <>
      <span className="text-slate-400 transition group-hover:text-cyan-600">{icon}</span>
      <span>{children}</span>
    </>
  );
  return (
    <li>
      {external ? (
        <a href={href} target="_blank" rel="noreferrer" className={cls}>
          {inner}
        </a>
      ) : (
        <Link href={href} className={cls}>
          {inner}
        </Link>
      )}
    </li>
  );
}

function FooterIcon({
  href,
  label,
  children,
}: {
  href: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      aria-label={label}
      title={label}
      className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition hover:-translate-y-0.5 hover:border-cyan-300 hover:text-cyan-700 hover:shadow-sm"
    >
      {children}
    </a>
  );
}

/* ───────────── Ribbon primitives ───────────── */

/**
 * Ribbon paneli — bir veya birden çok button + altta panel başlığı.
 * Panel içerik genişliğinde; ribbon container `justify-between` ile paneller
 * arasındaki boşluğu otomatik dağıtır. Panel başlığı (alt label) zaten
 * gruplamayı gösterdiği için dikey ayırıcı çizgiye gerek yok.
 */
function RibbonPanel({
  title, children, className,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        // Panel içerik genişliğinde (flex-1 yok); ribbon container `justify-between`
        // ile paneller arası boşluğu otomatik dağıtır. Dikey ayırıcı yok —
        // panel arası boşluk + altta panel başlığı zaten gruplamayı gösterir.
        "flex min-w-0 flex-col",
        className,
      )}
    >
      <div className="flex flex-1 items-stretch justify-center gap-2 px-4 pt-3 pb-2">
        {children}
      </div>
      <div className="border-t border-slate-200 px-3 py-1.5 text-center text-[10px] font-bold uppercase tracking-[0.18em] text-slate-400">
        {title}
      </div>
    </div>
  );
}

/**
 * Ribbon button — dikey: ikon üstte (cyan accent), label altta.
 * `onClick` veya `href` — biri kullanılır.
 * `active` — Language switcher gibi toggle button için.
 * `bareIcon` — true ise ikon kutusunun navy arka planı kaldırılır; bayrak
 * gibi self-contained görsel içerikler container'ı tam doldurur.
 */
function RibbonButton({
  href, onClick, icon, label, external = false, active = false, bareIcon = false,
}: {
  href?: string;
  onClick?: () => void;
  icon: React.ReactNode;
  label: string;
  external?: boolean;
  active?: boolean;
  bareIcon?: boolean;
}) {
  const classes = cn(
    // İçerik genişliğinde buton (flex-1 yok, min-w yok) — panel içinde
    // `justify-center` ile ortalanır. Komşu paneldeki butonla çakışmaz.
    "group flex flex-col items-center justify-start gap-2 rounded-lg",
    "px-3 py-2.5 text-center transition",
    active
      ? "bg-slate-100 ring-1 ring-slate-200 shadow-[0_4px_14px_-10px_rgba(15,23,42,0.3)]"
      : "hover:bg-slate-50 hover:shadow-[0_4px_14px_-10px_rgba(15,23,42,0.4)]",
  );
  const content = (
    <>
      <span className={cn(
        "flex h-14 w-14 items-center justify-center overflow-hidden rounded-xl transition",
        // İkon container — Hero'nun koyu navy paleti (parlak cyan yerine).
        // bareIcon=true (bayraklar) → arka plan yok, sadece outline/ring;
        // bareIcon=false (lucide ikonları) → navy chip / aktif cyan chip.
        bareIcon
          ? active
            ? "ring-2 ring-cyan-500 shadow-[0_6px_18px_-8px_rgba(12,40,71,0.55)]"
            : "ring-1 ring-slate-300/70 group-hover:ring-slate-400"
          : active
            ? "bg-cyan-600 text-white shadow-[0_6px_18px_-8px_rgba(12,40,71,0.6)]"
            : "bg-[#0c2847] text-cyan-200 group-hover:bg-[#173553] group-hover:text-cyan-100",
      )}>
        {icon}
      </span>
      <span className={cn(
        "inline-flex items-center gap-1 text-[13px] font-bold leading-none",
        active ? "text-slate-900" : "text-slate-700 group-hover:text-slate-900",
      )}>
        {label}
        {external && <ExternalLink className="h-3.5 w-3.5 text-slate-400" />}
      </span>
    </>
  );

  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={classes}>
        {content}
      </button>
    );
  }
  if (external && href) {
    return (
      <a href={href} target="_blank" rel="noreferrer" className={classes}>
        {content}
      </a>
    );
  }
  return (
    <Link href={href!} className={classes}>
      {content}
    </Link>
  );
}

/**
 * Dil göstergesi — kare bayrak SVG'leri (inline, dış bağımlılık yok).
 * `h-full w-full` ile parent ikon container'ını (56×56) tamamen kaplar;
 * RibbonButton `bareIcon` modunda navy arka plan görünmez, bayrak baskındır.
 *  • EN → Birleşik Krallık (Union Jack), 24×24 kare; çapraz+haç 45°.
 *  • TR → Türk bayrağı, 24×24 kare; al zemin + hilal + yıldız merkez.
 */
export function LocaleFlag({ code }: { code: Locale }) {
  return code === "tr" ? <FlagTR /> : <FlagGB />;
}

function FlagTR() {
  return (
    <svg
      viewBox="0 0 24 24"
      xmlns="http://www.w3.org/2000/svg"
      className="h-full w-full"
      preserveAspectRatio="xMidYMid slice"
      aria-label="Türkçe"
      role="img"
    >
      {/* Al zemin (resmi Türk bayrağı kırmızısı) */}
      <rect width="24" height="24" fill="#E30A17" />
      {/* Hilal: beyaz büyük daire üstüne kırmızı küçük daire (offset) */}
      <circle cx="8.5" cy="12" r="5" fill="#fff" />
      <circle cx="10" cy="12" r="4" fill="#E30A17" />
      {/* 5 köşeli beyaz yıldız (merkez 15, 12; R=2.2) — bir kolu sola bakar */}
      <polygon
        fill="#fff"
        points="12.8,12 14.32,11.51 14.32,9.91 15.26,11.2 16.78,10.71 15.84,12 16.78,13.29 15.26,12.8 14.32,14.09 14.32,12.49"
      />
    </svg>
  );
}

function FlagGB() {
  return (
    <svg
      viewBox="0 0 24 24"
      xmlns="http://www.w3.org/2000/svg"
      className="h-full w-full"
      preserveAspectRatio="xMidYMid slice"
      aria-label="English"
      role="img"
    >
      <clipPath id="gb-flag-clip">
        <rect width="24" height="24" />
      </clipPath>
      <g clipPath="url(#gb-flag-clip)">
        {/* Mavi zemin */}
        <rect width="24" height="24" fill="#012169" />
        {/* Beyaz çapraz (Saltire — Scotland) */}
        <path d="M0,0 L24,24 M24,0 L0,24" stroke="#fff" strokeWidth="5" />
        {/* Kırmızı çapraz ince (St. Patrick — Ireland) */}
        <path d="M0,0 L24,24 M24,0 L0,24" stroke="#C8102E" strokeWidth="2" />
        {/* Beyaz haç (St. George geniş, England) */}
        <path d="M12,0 V24 M0,12 H24" stroke="#fff" strokeWidth="8" />
        {/* Kırmızı haç (St. George dar üstte) */}
        <path d="M12,0 V24 M0,12 H24" stroke="#C8102E" strokeWidth="5" />
      </g>
    </svg>
  );
}
