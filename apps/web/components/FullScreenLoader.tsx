"use client";

/**
 * Full-screen brand loader — an animated GIF of the BibexPy logo mark.
 * File: /public/images/loader.gif
 *
 * Two usages:
 *  - overlay (fixed, covers the whole screen). Render it conditionally,
 *    bound to a state: FullScreenLoader is rendered while the loading
 *    expression is true.
 *  - Automatically on Next.js route transitions: rendered from loading.tsx.
 *
 * label is an optional sub-text; if omitted, only the logo spins.
 */
export function FullScreenLoader({ label }: { label?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      className="fixed inset-0 z-[100] flex items-center justify-center bg-ink/20 backdrop-blur-sm p-4"
    >
      {/* White modal card in the center */}
      <div className="flex flex-col items-center justify-center gap-4 rounded-2xl bg-white px-10 py-8 shadow-2xl ring-1 ring-black/5">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/images/loader.gif"
          alt="BibexPy"
          width={112}
          height={112}
          className="h-24 w-24 select-none object-contain"
          draggable={false}
        />
        {label && (
          <p className="text-sm font-semibold tracking-wide text-slate-500">{label}</p>
        )}
      </div>
      <span className="sr-only">Loading…</span>
    </div>
  );
}
