"use client";

/**
 * Tam ekran marka loader'ı — BibexPy logo simgesinin animasyonlu GIF'i.
 * Dosya: /public/images/loader.gif
 *
 * İki kullanım:
 *  - overlay (fixed, tüm ekranı kaplar). Bir state'e bağlı koşullu render et:
 *    loading ifadesi true iken FullScreenLoader render edilir.
 *  - Next.js route geçişlerinde otomatik: loading.tsx içinden render edilir.
 *
 * label opsiyonel alt-metin; verilmezse yalnız logo döner.
 */
export function FullScreenLoader({ label }: { label?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      className="fixed inset-0 z-[100] flex items-center justify-center bg-ink/20 backdrop-blur-sm p-4"
    >
      {/* Ortada beyaz modal kart */}
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
