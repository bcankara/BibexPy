/**
 * BibexPy v2 — Next.js config
 *
 * İki mod:
 *  - Dev (`npm run dev`)            → standart SSR, port 3000, ayrı backend (8001)
 *  - Build (`build:static`)         → static export, FastAPI içine gömülür, tek port
 *
 * `BIBEXPY_BUILD_MODE=static` çevre değişkeniyle export modu açılır.
 *
 * API adresi: ARTIK build-time env'den OKUNMUYOR. `lib/api-client.ts` runtime'da
 * window.location origin'inden tespit ediyor (dev 300x → 8001, paket → relative /api).
 * Bu yüzden eski `env: { NEXT_PUBLIC_API_BASE }` bloğu kaldırıldı — .env.local'daki
 * dev adresinin static export'a gömülüp "Failed to fetch" yaratması engellendi.
 *
 * Dinamik route'lar (`projects/[id]/...`) generateStaticParams placeholder `_` ile
 * üretilir; FastAPI gerçek-ID URL'lerini `_` HTML'ine serve eder. Gerçek ID
 * client-side `lib/use-project-id.ts` (window.location.pathname) ile okunur.
 */
/** @type {import('next').NextConfig} */
const isStaticBuild = process.env.BIBEXPY_BUILD_MODE === "static";

const nextConfig = {
  reactStrictMode: true,
  experimental: { typedRoutes: false },
  // Static export — paketleme için
  ...(isStaticBuild && {
    output: "export",
    // <img> optimizasyonu Next sunucusu gerektirir; export'ta kapalı olmalı.
    images: { unoptimized: true },
    // Trailing slash → out/projects/_/upload/index.html (dizin yapısı) — FastAPI
    // catch-all'ı için daha kolay.
    trailingSlash: true,
    // Build sırasında type-check ve lint'i atla — bunlar dev'de `npm run typecheck`
    // ve editor üzerinden zaten kontrol ediliyor. Build'in amacı sadece çalışan
    // static asset üretmek; check'ler build'i kilitlerse paketleme akışı tıkanır.
    typescript: { ignoreBuildErrors: true },
    eslint: { ignoreDuringBuilds: true },
  }),
};

module.exports = nextConfig;
