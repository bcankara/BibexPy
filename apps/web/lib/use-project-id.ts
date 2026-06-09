"use client";

import { useParams } from "next/navigation";

/**
 * Proje ID'sini güvenilir biçimde okur — hem `npm run dev` (gerçek SSR) hem
 * `bibexpy` paketi (Next.js static export) için.
 *
 * SORUN: Static export'ta dinamik route'lar `generateStaticParams` placeholder'ı
 * `_` ile üretilir; server `params` prop'u build-time'da `"_"`'ye sabitlenir.
 * DEV modunda ise route gerçek olduğundan `useParams()` doğru ID'yi verir.
 *
 * STRATEJİ (öncelik sırası):
 *  1. `useParams().id` — DEV'de hem sunucu hem client'ta GERÇEK ID döner; bu
 *     yüzden hydration uyuşmazlığı OLMAZ. (Bu hook'u birincil yapmamızın sebebi.)
 *  2. `useParams` placeholder `_` döndürürse (yalnızca static export build/SSR)
 *     → canlı URL'den (`window.location.pathname`) gerçek ID'yi al.
 *  3. Hiçbiri yoksa `_` (build zamanı; client'ta asla bu dala düşülmez).
 *
 * Böylece DEV'de konsol temiz (uyuşmazlık yok, "_" fetch yok), static export'ta
 * da paket düzgün çalışır.
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
