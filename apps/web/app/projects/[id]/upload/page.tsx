"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useProjectId } from "@/lib/use-project-id";

/**
 * The old "Preparation" (/upload) step has been merged into "Data & Merge"
 * (/merge) (upload + Smart Merge are a single step; there is no separate
 * Prepare). This route redirects to /merge for backward compatibility and for
 * old bookmarks. Since the route is kept in the static export, the file is
 * not deleted; a redirecting stub is left in its place.
 */
export default function UploadRedirect() {
  const id = useProjectId();
  const router = useRouter();
  useEffect(() => {
    if (id && id !== "_") router.replace(`/projects/${id}/merge`);
  }, [id, router]);
  return null;
}
