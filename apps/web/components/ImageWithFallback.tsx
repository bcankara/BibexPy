"use client";
/**
 * Component that renders a fallback when an image fails to load.
 *
 * Usage:
 *   <ImageWithFallback
 *     src="/images/hero.png"
 *     alt="Hero"
 *     className="rounded-xl"
 *     fallback={<div className="bg-bg-soft" />}
 *   />
 *
 * If src returns 404 or otherwise fails to load, the fallback is rendered. It is
 * used to show an elegant placeholder when the image files are not present under
 * `public/images/`.
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
