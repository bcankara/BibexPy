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
