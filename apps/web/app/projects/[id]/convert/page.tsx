"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useProjectId } from "@/lib/use-project-id";

/**
 * Static-export-compatible redirect:
 * Redirects the old "convert" tab → the "Data & Merge" (/merge) page.
 * The project ID is read from the URL (useProjectId — reliable in a static export).
 */
export default function ConvertRedirect() {
  const id = useProjectId();
  const router = useRouter();
  useEffect(() => {
    if (id && id !== "_") router.replace(`/projects/${id}/merge`);
  }, [id, router]);
  return null;
}
