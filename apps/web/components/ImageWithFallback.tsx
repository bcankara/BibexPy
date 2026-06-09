"use client";
/**
 * Görsel yüklenemediğinde fallback render eden component.
 *
 * Kullanım:
 *   <ImageWithFallback
 *     src="/images/hero.png"
 *     alt="Hero"
 *     className="rounded-xl"
 *     fallback={<div className="bg-bg-soft" />}
 *   />
 *
 * src 404 olursa veya yüklenemezse fallback render edilir. Görsel dosyaları
 * `public/images/` altında bulunmadığında elegant bir placeholder göstermek
 * için kullanılır.
 */
import { useState } from "react";
import { cn } from "@/lib/cn";

type Props = {
  src: string;
  alt: string;
  className?: string;
  fallback: React.ReactNode;
  imgProps?: React.ImgHTMLAttributes<HTMLImageElement>;
};

export function ImageWithFallback({ src, alt, className, fallback, imgProps }: Props) {
  const [failed, setFailed] = useState(false);
  if (failed) return <>{fallback}</>;
  return (
    <img
      src={src}
      alt={alt}
      onError={() => setFailed(true)}
      className={cn("block", className)}
      {...imgProps}
    />
  );
}
