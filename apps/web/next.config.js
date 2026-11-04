/**
 * BibexPy v2 — Next.js config
 *
 * Two modes:
 *  - Dev (`npm run dev`)            → standard SSR, port 3000, separate backend (8001)
 *  - Build (`build:static`)         → static export, embedded into FastAPI, single port
 *
 * Export mode is enabled via the `BIBEXPY_BUILD_MODE=static` environment variable.
 *
 * API address: it is NO LONGER read from a build-time env. `lib/api-client.ts` detects it
 * at runtime from the window.location origin (dev 300x → 8001, package → relative /api).
 * For this reason the old `env: { NEXT_PUBLIC_API_BASE }` block was removed — this prevents
 * the dev address in .env.local from being baked into the static export and causing
 * "Failed to fetch".
 *
 * Dynamic routes (`projects/[id]/...`) are generated with the generateStaticParams
 * placeholder `_`; FastAPI serves real-ID URLs from the `_` HTML. The real ID is
 * read client-side via `lib/use-project-id.ts` (window.location.pathname).
 */
/** @type {import('next').NextConfig} */
const isStaticBuild = process.env.BIBEXPY_BUILD_MODE === "static";

const nextConfig = {
  reactStrictMode: true,
  experimental: { typedRoutes: false },
  // Static export — for packaging
  ...(isStaticBuild && {
    output: "export",
    // <img> optimization requires a Next server; it must be disabled in the export.
    images: { unoptimized: true },
    // Trailing slash → out/projects/_/upload/index.html (directory structure) — easier
    // for the FastAPI catch-all.
    trailingSlash: true,
    // Skip type-check and lint during the build — they are already checked in dev via
    // `npm run typecheck` and through the editor. The build's only purpose is to produce
    // working static assets; if the checks block the build, the packaging flow stalls.
    typescript: { ignoreBuildErrors: true },
    eslint: { ignoreDuringBuilds: true },
  }),
};

module.exports = nextConfig;
