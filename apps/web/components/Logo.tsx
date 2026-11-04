/**
 * Official BibexPy logo (a single image — the PNG lockup supplied by the user).
 *
 * Variants:
 *  • "header" (default) — SQUARE/stacked lockup: atom on top + BIBEXPY + "V2.0.0
 *    Helium" badge (bottom right).  Compact, for the inner-page header.  ~1.35:1.
 *  • "inner"          — atom + BIBEXPY + tagline + VERTICAL "V2.0.0 Helium" pill
 *    (on the right).  Horizontal lockup for the footer.  1526×378 (≈4.04:1).
 *  • "full"           — atom + BIBEXPY + tagline + HORIZONTAL "V2.0.0 Helium" badge
 *    (top right).  For the homepage hero (outer/landing).  3120×900.
 *
 * `size` = height (px); the width is computed from the variant's aspect ratio.
 */
type Variant = "header" | "inner" | "full";

const VARIANTS: Record<Variant, { src: string; ratio: number; alt: string }> = {
  header: {
    src: "/images/bibexpy-logo-header.png",
    ratio: 2047 / 1521, // ≈ 1.346
    alt: "BibexPy — V2.0.0 Helium",
  },
  inner: {
    src: "/images/bibexpy-logo-in.png",
    ratio: 1526 / 378, // ≈ 4.037
    alt: "BibexPy — V2.0.0 Helium — Bibliometrics Experience with Python",
  },
  full: {
    src: "/images/bibexpy-logo-full.png",
    ratio: 3120 / 900, // ≈ 3.467
    alt: "BibexPy — V2.0.0 Helium — Bibliometrics Experience with Python",
  },
};

type Props = { size?: number; variant?: Variant; className?: string };

export function Logo({ size = 28, variant = "header", className }: Props) {
  const { src, ratio, alt } = VARIANTS[variant];
  const width = Math.round(size * ratio);
  return (
    <img
      src={src}
      alt={alt}
      width={width}
      height={size}
      draggable={false}
      className={`select-none${className ? ` ${className}` : ""}`}
      style={{ height: size, width }}
    />
  );
}
