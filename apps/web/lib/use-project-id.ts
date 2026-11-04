"use client";

import { useParams } from "next/navigation";

/**
 * Reads the project ID reliably — for both `npm run dev` (real SSR) and the
 * `bibexpy` package (Next.js static export).
 *
 * PROBLEM: In a static export, dynamic routes are generated with the
 * `generateStaticParams` placeholder `_`; the server `params` prop is pinned
 * to `"_"` at build time. In DEV mode the route is real, so `useParams()`
 * returns the correct ID.
 *
 * STRATEGY (in priority order):
 *  1. `useParams().id` — in DEV it returns the REAL ID on both the server and
 *     the client, so there is NO hydration mismatch. (This is why we made this
 *     hook the primary source.)
 *  2. If `useParams` returns the placeholder `_` (static export build/SSR only)
 *     → take the real ID from the live URL (`window.location.pathname`).
 *  3. If neither is available, `_` (build time; the client never reaches this
 *     branch).
 *
 * This way the console stays clean in DEV (no mismatch, no "_" fetch), and the
 * package also works correctly in a static export.
 */
export function useProjectId(): string {
  const params = useParams<{ id: string }>();
  const fromParams = params?.id;
  if (fromParams && fromParams !== "_") {
    return decodeURIComponent(fromParams);
  }
  if (typeof window !== "undefined") {
    const m = window.location.pathname.match(/\/projects\/([^/]+)/);
    if (m && m[1] && m[1] !== "_") return decodeURIComponent(m[1]);
  }
  return fromParams ?? "_";
}
