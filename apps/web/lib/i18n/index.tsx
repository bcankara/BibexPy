"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import en from "./en.json";
import tr from "./tr.json";

export type Locale = "en" | "tr";
export const LOCALES: Locale[] = ["en", "tr"];
export const DEFAULT_LOCALE: Locale = "en";

const dictionaries: Record<Locale, Record<string, unknown>> = { en, tr };

type I18nContextValue = {
  locale: Locale;
  setLocale: (l: Locale) => void;
  /** Get a translation by dot-path (e.g. "analyses.title"). Supports {var} interpolation. */
  t: (key: string, vars?: Record<string, string | number>) => string;
  /** Get an array of strings (for lists like howItWorks, useCases). */
  tArr: (key: string) => string[];
  /** Get an array of objects (for outputs lists, etc.) — pass type via generic. */
  tObjArr: <T = Record<string, string>>(key: string) => T[];
};

const I18nContext = createContext<I18nContextValue | null>(null);

const STORAGE_KEY = "bibexpy.locale";

function resolvePath(obj: unknown, path: string): unknown {
  if (!obj || typeof obj !== "object") return undefined;
  let cur: unknown = obj;
  for (const part of path.split(".")) {
    if (cur && typeof cur === "object" && part in (cur as Record<string, unknown>)) {
      cur = (cur as Record<string, unknown>)[part];
    } else {
      return undefined;
    }
  }
  return cur;
}

function interpolate(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (_, key) => {
    return key in vars ? String(vars[key]) : `{${key}}`;
  });
}

export function I18nProvider({ children, initialLocale }: { children: React.ReactNode; initialLocale?: Locale }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale ?? DEFAULT_LOCALE);

  // İlk yüklemede localStorage'den oku (client-side only)
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "en" || stored === "tr") {
      setLocaleState(stored);
    }
  }, []);

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, l);
      // <html lang="..."> da güncellensin
      document.documentElement.lang = l;
    }
  }, []);

  const t = useCallback(
    (key: string, vars?: Record<string, string | number>): string => {
      const dict = dictionaries[locale];
      let value = resolvePath(dict, key);
      // Fallback: en
      if (typeof value !== "string" && locale !== DEFAULT_LOCALE) {
        value = resolvePath(dictionaries[DEFAULT_LOCALE], key);
      }
      if (typeof value !== "string") {
        // Geliştirici görsün diye key'i göster (üretimde silent fallback olur)
        return key;
      }
      return interpolate(value, vars);
    },
    [locale],
  );

  const tArr = useCallback((key: string): string[] => {
    const dict = dictionaries[locale];
    let value = resolvePath(dict, key);
    if (!Array.isArray(value) && locale !== DEFAULT_LOCALE) {
      value = resolvePath(dictionaries[DEFAULT_LOCALE], key);
    }
    if (Array.isArray(value)) return value.filter((x) => typeof x === "string");
    return [];
  }, [locale]);

  const tObjArr = useCallback(<T = Record<string, string>,>(key: string): T[] => {
    const dict = dictionaries[locale];
    let value = resolvePath(dict, key);
    if (!Array.isArray(value) && locale !== DEFAULT_LOCALE) {
      value = resolvePath(dictionaries[DEFAULT_LOCALE], key);
    }
    if (Array.isArray(value)) return value as T[];
    return [];
  }, [locale]);

  const value = useMemo<I18nContextValue>(
    () => ({ locale, setLocale, t, tArr, tObjArr }),
    [locale, setLocale, t, tArr, tObjArr],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) {
    // Provider yoksa default'larla çalışan stub döner — uygulama bozulmaz
    return {
      locale: DEFAULT_LOCALE,
      setLocale: () => {},
      t: (key: string, vars?: Record<string, string | number>) => {
        const value = resolvePath(dictionaries[DEFAULT_LOCALE], key);
        if (typeof value !== "string") return key;
        return interpolate(value, vars);
      },
      tArr: (key: string) => {
        const v = resolvePath(dictionaries[DEFAULT_LOCALE], key);
        return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
      },
      tObjArr: <T = Record<string, string>,>(key: string): T[] => {
        const v = resolvePath(dictionaries[DEFAULT_LOCALE], key);
        return Array.isArray(v) ? (v as T[]) : [];
      },
    };
  }
  return ctx;
}

/** Shortcut hook: `const t = useT(); t("nav.data")` */
export function useT() {
  return useI18n().t;
}
