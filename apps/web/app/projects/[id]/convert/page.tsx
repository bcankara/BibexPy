"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useProjectId } from "@/lib/use-project-id";

/**
 * Static export uyumlu redirect:
 * Eski "convert" sekmesi → "Veri & Birleştirme" (/merge) sayfasına yönlendirir.
 * Proje ID'si URL'den okunur (useProjectId — static export'ta güvenilir).
 */
export default function ConvertRedirect() {
  const id = useProjectId();
  const router = useRouter();
  useEffect(() => {
    if (id && id !== "_") router.replace(`/projects/${id}/merge`);
  }, [id, router]);
  return null;
}
