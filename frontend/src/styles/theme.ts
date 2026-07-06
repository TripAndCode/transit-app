export type Theme = "light" | "dark";

const PREF_KEY = "transit.theme";
const DEFAULT_THEME: Theme = "dark";

/** Read the persisted theme. Defaults to dark (the new default) when unset
 *  or invalid — not prefers-color-scheme-based; this is a product choice,
 *  not an OS-driven mode. */
export function readThemePref(): Theme {
  try {
    const v = localStorage.getItem(PREF_KEY);
    if (v === "light" || v === "dark") return v;
  } catch {
    /* localStorage unavailable — fall through */
  }
  return DEFAULT_THEME;
}

/** Persist the chosen theme. No-ops if localStorage is unavailable. */
export function writeThemePref(theme: Theme): void {
  try {
    localStorage.setItem(PREF_KEY, theme);
  } catch {
    /* ignore */
  }
}

/** Apply the theme to the document by setting data-theme on <html>, which
 *  the global.css `:root[data-theme="dark"]` block selects on. */
export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}
