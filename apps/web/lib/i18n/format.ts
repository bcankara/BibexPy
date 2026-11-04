import type { Locale } from "./index";

/**
 * Locale-aware number/date formatting driven by the APP's selected language
 * (`useI18n().locale`), never by the browser's locale. Passing `undefined` to
 * `toLocaleString` makes the output depend on the browser, so an English text
 * could contain a Turkish thousands separator ("1.192") or a Turkish month name.
 *
 * Usage: `const { locale } = useI18n(); formatNumber(1192, locale)`.
 */

/** Number locale per app language: en -> "1,192", tr -> "1.192". */
const NUMBER_LOCALE: Record<Locale, string> = { en: "en-US", tr: "tr-TR" };

/**
 * Date locale per app language. English uses en-GB so the day comes first and the
 * month is spelled out ("5 October 2026"), which cannot be misread the way a
 * numeric 10/07/2026 can.
 */
const DATE_LOCALE: Record<Locale, string> = { en: "en-GB", tr: "tr-TR" };

const EMPTY = "—";

function isValid(d: Date): boolean {
  return !Number.isNaN(d.getTime());
}

/** Format a number with the app language's digit grouping and decimal separator. */
export function formatNumber(n: number, locale: Locale, options?: Intl.NumberFormatOptions): string {
  // A missing/NaN value from the API renders as a dash instead of "NaN" or a thrown TypeError.
  if (typeof n !== "number" || Number.isNaN(n)) return EMPTY;
  return n.toLocaleString(NUMBER_LOCALE[locale], options);
}

/** Calendar date with the month spelled out, e.g. "5 October 2026" / "5 Ekim 2026". */
export function formatDate(d: Date, locale: Locale): string {
  if (!isValid(d)) return EMPTY;
  return d.toLocaleDateString(DATE_LOCALE[locale], { day: "numeric", month: "long", year: "numeric" });
}

/** Date and time, e.g. "5 Oct 2026, 14:05" / "5 Eki 2026 14:05". */
export function formatDateTime(d: Date, locale: Locale): string {
  if (!isValid(d)) return EMPTY;
  return d.toLocaleString(DATE_LOCALE[locale], {
    day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

/** File size with the app language's decimal separator, e.g. "1.5 MB" / "1,5 MB". */
export function formatBytes(n: number, locale: Locale): string {
  if (!Number.isFinite(n) || n < 0) return EMPTY;
  const one = { minimumFractionDigits: 1, maximumFractionDigits: 1 };
  if (n < 1024) return `${formatNumber(n, locale)} B`;
  if (n < 1024 ** 2) return `${formatNumber(n / 1024, locale, one)} KB`;
  if (n < 1024 ** 3) return `${formatNumber(n / 1024 ** 2, locale, one)} MB`;
  return `${formatNumber(n / 1024 ** 3, locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} GB`;
}

/**
 * Percentage from a 0..100 value in the app language's own notation:
 * en "45.5%", tr "%45,5" (Intl places the sign per locale).
 */
export function formatPercent(pct: number, locale: Locale, fractionDigits = 1): string {
  if (typeof pct !== "number" || !Number.isFinite(pct)) return EMPTY;
  return (pct / 100).toLocaleString(NUMBER_LOCALE[locale], {
    style: "percent",
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  });
}

/** Epoch seconds (backend timestamps) -> Date. */
export function fromEpochSeconds(ts: number): Date {
  return new Date(ts * 1000);
}
