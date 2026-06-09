"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useProjectId } from "@/lib/use-project-id";

/**
 * Static export uyumlu redirect:
 * Eski "disambiguate" sekmesi → AI Asistan içinde alt-tab olarak yaşar.
 * Proje ID'si URL'den okunur (useProjectId — static export'ta güvenilir).
 */
export default function DisambiguateRedirect() {
  const id = useProjectId();
  const router = useRouter();
  useEffect(() => {
    if (id && id !== "_") router.replace(`/projects/${id}/enrich?tab=disambiguate`);
  }, [id, router]);
  return null;
}
