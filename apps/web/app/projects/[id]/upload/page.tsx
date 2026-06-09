"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useProjectId } from "@/lib/use-project-id";

/**
 * Eski "Hazırlık" (/upload) adımı, "Veri & Birleştirme" (/merge) içinde birleşti
 * (yükleme + Smart Merge tek adım; ayrı Prepare yok). Bu rota geriye uyumluluk +
 * eski yer imleri için /merge'e yönlendirir. Statik export'ta rota korunur diye
 * dosya silinmez, yönlendiren bir stub bırakılır.
 */
export default function UploadRedirect() {
  const id = useProjectId();
  const router = useRouter();
  useEffect(() => {
    if (id && id !== "_") router.replace(`/projects/${id}/merge`);
  }, [id, router]);
  return null;
}
