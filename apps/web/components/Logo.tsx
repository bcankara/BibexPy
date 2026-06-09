/**
 * BibexPy resmi logosu (tek görsel — kullanıcı tarafından sağlanan PNG lockup).
 *
 * Varyantlar:
 *  • "header" (vars.) — KARE/yığılmış lockup: atom üstte + BIBEXPY + "V2.0.0
 *    Helium" rozeti (sağ alt).  İç sayfa header'ı için kompakt.  ~1.35:1.
 *  • "inner"          — atom + BIBEXPY + slogan + DİKEY "V2.0.0 Helium" pill'i
 *    (sağda).  Footer için yatay lockup.  1526×378 (≈4.04:1).
 *  • "full"           — atom + BIBEXPY + slogan + YATAY "V2.0.0 Helium" rozeti
 *    (sağ üst).  Anasayfa hero (dış/landing) için.  3120×900.
 *
 * `size` = yükseklik (px); genişlik varyant oranından hesaplanır.
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
