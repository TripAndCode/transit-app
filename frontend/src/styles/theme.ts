import { useEffect, useState } from "react";

export type Theme = "light" | "dark";

/** Event name applyTheme dispatches on `window` when the theme changes, so
 *  imperative (non-CSS) consumers can react. DOM/CSS consumers recolor via the
 *  cascade for free and don't need it. */
export const THEME_CHANGE_EVENT = "themechange";

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
 *  the global.css `:root[data-theme="dark"]` block selects on. Also dispatches
 *  a `themechange` event so imperative consumers that can't recolor via the CSS
 *  cascade (the MapLibre layer hooks, which embed severeColorResolved() in style
 *  expressions) can rebuild — see useThemeSignal. Skips redundant writes if the
 *  theme value is unchanged. */
export function applyTheme(theme: Theme): void {
  if (document.documentElement.dataset.theme !== theme) {
    document.documentElement.dataset.theme = theme;
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent<Theme>(THEME_CHANGE_EVENT, { detail: theme }));
    }
  }
}

/** Current theme as a re-render signal for imperative (non-CSS) consumers.
 *  Subscribes to the `themechange` event applyTheme dispatches, so a hook that
 *  puts this value in an effect's dependency array re-runs on a theme toggle —
 *  without every such component mounting its own useTheme(). CSS consumers
 *  don't need this: the cascade recolors var(--*) references automatically. */
export function useThemeSignal(): Theme {
  const [theme, setTheme] = useState<Theme>(() =>
    typeof document !== "undefined" && document.documentElement.dataset.theme === "dark"
      ? "dark"
      : "light",
  );
  useEffect(() => {
    function onChange(e: Event) {
      const detail = (e as CustomEvent<Theme>).detail;
      setTheme(detail === "dark" ? "dark" : "light");
    }
    window.addEventListener(THEME_CHANGE_EVENT, onChange);
    return () => window.removeEventListener(THEME_CHANGE_EVENT, onChange);
  }, []);
  return theme;
}
