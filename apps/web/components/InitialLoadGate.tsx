"use client";
import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { FullScreenLoader } from "./FullScreenLoader";
import { cn } from "@/lib/cn";

/**
 * Yükleme kapısı — HEM ilk açılışta HEM her route geçişinde loader gösterir.
 *
 * pathname her değiştiğinde (ve ilk mount'ta) loader yeniden görünür. Loader,
 * şu İKİSİNDEN GEÇ OLANA kadar kalır:
 *   (a) ilk açılışta `window` "load" olayı (tüm kaynaklar inene kadar; sonraki
 *       client-side geçişlerde bu zaten tamamdır),
 *   (b) MIN_MS minimum süre (sayfa anında hazır olsa bile loader göz kırpmaz).
 * Sonra 300ms fade ile kaybolur. Güvenlik için 12 sn sonra her halükârda kapanır.
 *
 * Tek kaynak: app/**loading.tsx kaldırıldı; route geçişi de buradan yönetilir
 * (Suspense fallback min süre garanti edemiyordu, anında kayboluyordu).
 */
const MIN_MS = 1000; // sayfa hemen yüklense bile loader en az bu kadar görünür

export function InitialLoadGate() {
  const pathname = usePathname();
  const [phase, setPhase] = useState<"loading" | "fading" | "gone">("loading");
  const firstRef = useRef(true);

  useEffect(() => {
    setPhase("loading");
    const start = performance.now();
    const isFirst = firstRef.current;
    firstRef.current = false;

    let loaded = !isFirst || document.readyState === "complete";
    let minElapsed = false;

    const tryFinish = () => {
      if (loaded && minElapsed) setPhase((p) => (p === "loading" ? "fading" : p));
    };
    const onLoad = () => { loaded = true; tryFinish(); };

    if (!loaded) window.addEventListener("load", onLoad, { once: true });
    const minTimer = setTimeout(() => { minElapsed = true; tryFinish(); }, MIN_MS);
    const safety = setTimeout(() => setPhase((p) => (p === "loading" ? "fading" : p)), 12000);

    return () => {
      window.removeEventListener("load", onLoad);
      clearTimeout(minTimer);
      clearTimeout(safety);
    };
  }, [pathname]);

  if (phase === "gone") return null;
  return (
    <div
      className={cn(
        "transition-opacity duration-300",
        phase === "fading" ? "pointer-events-none opacity-0" : "opacity-100",
      )}
      onTransitionEnd={() => setPhase((p) => (p === "fading" ? "gone" : p))}
    >
      <FullScreenLoader />
    </div>
  );
}
