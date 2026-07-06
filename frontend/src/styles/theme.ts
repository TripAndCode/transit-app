import { useSyncExternalStore } from "react";

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

/** Subscribe to theme changes: re-run `onStoreChange` whenever applyTheme
 *  dispatches its `themechange` event. Returns the unsubscribe cleanup. */
function subscribeThemeSignal(onStoreChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(THEME_CHANGE_EVENT, onStoreChange);
  return () => window.removeEventListener(THEME_CHANGE_EVENT, onStoreChange);
}

/** Snapshot of the current theme, read from the DOM (`data-theme` on <html>) —
 *  the single source of truth applyTheme writes. Returns DEFAULT_THEME (dark)
 *  when the attribute is absent or unrecognized, matching the module's default
 *  rather than a hardcoded "light". Returns a stable primitive so
 *  useSyncExternalStore won't loop. Doubles as the server snapshot. */
function themeSnapshot(): Theme {
  if (typeof document === "undefined") return DEFAULT_THEME;
  const v = document.documentElement.dataset.theme;
  return v === "light" || v === "dark" ? v : DEFAULT_THEME;
}

/** Current theme as a re-render signal for imperative (non-CSS) consumers.
 *  A hook that puts this value in an effect's dependency array re-runs on a
 *  theme toggle — without every such component mounting its own useTheme().
 *  CSS consumers don't need this: the cascade recolors var(--*) references
 *  automatically.
 *
 *  Backed by useSyncExternalStore, which reads the snapshot at subscribe time
 *  (no missed-update window between the initial read and the listener
 *  attaching, unlike a useState + useEffect pair) and stays concurrent-safe. */
export function useThemeSignal(): Theme {
  return useSyncExternalStore(subscribeThemeSignal, themeSnapshot, themeSnapshot);
}
