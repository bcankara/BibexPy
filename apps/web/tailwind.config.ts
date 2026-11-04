import type { Config } from "tailwindcss";

// ─────────────────────────────────────────────────────────────────────────────
// BibexPy v2 "Helium" CORPORATE PALETTE (user's table — matches the logo):
//   Primary color   : dark navy #0c2847 (dark tone #081c32 → header/top menu)
//   Secondary color : purple #4f1964 (dark #371246 secondary button, light #ede8f0 label)
//   Accent / CTA    : petrol green #0f766e
//   Statuses        : success #15803d · warning #a16207 · danger #b91c1c (matte/dark)
//   Background/text : bg #f7f9fc · card #fff · border #d8dee8 · text #172033/#5f6f85
//
// The scales are also assigned to Tailwind's raw family names (override) → the hundreds of
// `cyan-*`/`emerald-*`/... utilities in the code switch to the corporate palette without
// touching the files.
// ─────────────────────────────────────────────────────────────────────────────
const navy = {
  50: "#f4f7fa", 100: "#e7edf4", 200: "#c9d6e5", 300: "#a2b8d0", 400: "#7593b5",
  500: "#51729a", 600: "#38597f", 700: "#264667", 800: "#173553", 900: "#0c2847", 950: "#081c32",
};
const grape = {
  50: "#f6f2f8", 100: "#ede8f0", 200: "#ddd0e3", 300: "#c3aed0", 400: "#a983bb",
  500: "#8e5ba5", 600: "#74398c", 700: "#602a76", 800: "#57226c", 900: "#4f1964", 950: "#371246",
};
const petrol = {
  50: "#eef7f6", 100: "#d5ecea", 200: "#abd8d4", 300: "#79bcb6", 400: "#4a9e97",
  500: "#28837b", 600: "#0f766e", 700: "#0d635d", 800: "#0c514c", 900: "#0b433f", 950: "#062a27",
};
const green = {
  50: "#f1f8f3", 100: "#ddefe2", 200: "#b9dec4", 300: "#8cc69e", 400: "#57a677",
  500: "#2f8f56", 600: "#15803d", 700: "#136a34", 800: "#12552c", 900: "#104726", 950: "#082b16",
};
const amber = {
  50: "#faf6ea", 100: "#f3e8c8", 200: "#e7d18e", 300: "#d7b355", 400: "#c8982f",
  500: "#b67f1a", 600: "#a16207", 700: "#855208", 800: "#6d440c", 900: "#5c3a0e", 950: "#352007",
};
const red = {
  50: "#fcf1f1", 100: "#f8dcdc", 200: "#efb9b9", 300: "#e18d8d", 400: "#d05c5c",
  500: "#c43a3a", 600: "#b91c1c", 700: "#9a1818", 800: "#7f1717", 900: "#6b1717", 950: "#420c0c",
};

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Surface & background — table: bg #f7f9fc, card hover/light tone #e7eaed.
        bg: { DEFAULT: "#f7f9fc", soft: "#e7eaed", card: "#FFFFFF" },
        ink: { DEFAULT: "#172033", soft: "#243049" },     // main text / dark-surface text
        muted: "#5f6f85",
        border: "#d8dee8",

        // Brand — PRIMARY navy (#0c2847). `cyan` is also OVERRIDDEN with this scale →
        // all `cyan-*` accents in the code automatically turn navy.
        brand: navy,
        cyan: navy, blue: navy, sky: navy, indigo: navy,

        // Secondary PURPLE (#4f1964 — the Helium badge in the logo). The `violet/purple/
        // fuchsia-*` utilities come from this scale (label/secondary accent).
        violet: grape, purple: grape, fuchsia: grape,

        // Accent / CTA — petrol (#0f766e). `teal-*` comes from this scale.
        teal: petrol,

        // Status families — scales anchored to the matte/dark semantics in the table.
        emerald: green, green: green, lime: green,
        red: red, rose: red,
        amber: amber, yellow: amber, orange: amber,

        primary: { DEFAULT: "#0c2847", hover: "#081c32" },  // primary / dark tone
        accent:  { DEFAULT: "#0f766e", hover: "#0d635d" },  // CTA petrol

        // Semantic tokens — table values (DEFAULT) + light background (soft).
        success: { DEFAULT: "#15803d", soft: green[100] },
        warning: { DEFAULT: "#a16207", soft: amber[100] },
        danger:  { DEFAULT: "#b91c1c", soft: red[100] },
        info:    { DEFAULT: navy[600], soft: navy[100] },
      },
      fontFamily: {
        sans: ['Manrope', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      boxShadow: {
        card: "0 1px 3px 0 rgba(12, 40, 71, 0.06), 0 1px 2px 0 rgba(12, 40, 71, 0.04)",
        soft: "0 4px 12px -2px rgba(12, 40, 71, 0.08)",
      },
      backgroundImage: {
        // Token kept for backward compatibility — it is no longer a gradient; the
        // primary dark navy is applied as a flat color. In new usages, use `bg-[#0c2847]` directly.
        "brand-gradient":
          "linear-gradient(180deg, #0c2847 0%, #0c2847 100%)",
      },
    },
  },
  plugins: [],
};

export default config;
