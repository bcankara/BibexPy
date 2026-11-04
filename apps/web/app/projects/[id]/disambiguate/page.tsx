"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useProjectId } from "@/lib/use-project-id";

/**
 * Static-export-compatible redirect:
 * The old "disambiguate" tab → lives on as a sub-tab inside the AI Assistant.
 * The project ID is read from the URL (useProjectId — reliable in a static export).
 */
export default function DisambiguateRedirect() {
  const id = useProjectId();
  const router = useRouter();
  useEffect(() => {
    if (id && id !== "_") router.replace(`/projects/${id}/enrich?tab=disambiguate`);
  }, [id, router]);
  return null;
}
