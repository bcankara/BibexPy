"use client";
import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { FullScreenLoader } from "./FullScreenLoader";
import { cn } from "@/lib/cn";

/**
 * Loading gate — shows the loader on BOTH the first open AND every route change.
 *
 * Whenever pathname changes (and on the first mount) the loader appears again.
 * The loader stays until the LATER of these TWO:
 *   (a) on first open, the `window` "load" event (until all resources have been
 *       downloaded; on later client-side transitions this is already complete),
 *   (b) the MIN_MS minimum duration (so the loader does not flicker even if the
 *       page is ready instantly).
 * Then it disappears with a 300ms fade. For safety it closes anyway after 12 s.
 *
 * Single source: app/**loading.tsx was removed; route transitions are handled
 * here too (the Suspense fallback could not guarantee a minimum duration, it
 * vanished instantly).
 */
const MIN_MS = 1000; // the loader stays visible at least this long even if the page loads immediately

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
