"use client";
import { HelpCircle } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * Small help icon (?) + an explanation bubble that opens on hover/focus.
 * Dependency-free (pure CSS group-hover/focus) → safe for static export and SSR.
 * For explaining unfamiliar metrics (Blocking threshold, Jaro-Winkler, etc.) in place.
 */
export function InfoTip({ text, className }: { text: string; className?: string }) {
  return (
    <span className={cn("relative inline-flex group align-middle", className)}>
      <button
        type="button"
        aria-label={text}
        className="text-muted hover:text-brand-600 focus:text-brand-600 focus:outline-none cursor-help"
      >
        <HelpCircle className="h-3.5 w-3.5" />
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute left-1/2 bottom-full z-50 mb-1 hidden w-56 max-w-[15rem] -translate-x-1/2 whitespace-normal rounded-md bg-ink px-2 py-1.5 text-[11px] font-normal leading-snug text-white shadow-soft group-hover:block group-focus-within:block"
      >
        {text}
      </span>
    </span>
  );
}
