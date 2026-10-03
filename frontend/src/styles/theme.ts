import { useSyncExternalStore } from "react";

/** What the user chose. `"system"` defers to the OS appearance. */
export type Theme = "light" | "dark" | "system";

/** What actually gets painted. `data-theme` on <html> only ever holds one of
 *  these — global.css selects on `:root[data-theme="dark"]`, so there is no
 *  such thing as a "system" stylesheet. */
type ResolvedTheme = "light" | "dark";

/** Event name applyTheme dispatches on `window` when the theme changes, so
 *  imperative (non-CSS) consumers can react. DOM/CSS consumers recolor via the
 *  cascade for free and don't need it. */
const THEME_CHANGE_EVENT = "themechange";

const PREF_KEY = "transit.theme";
/** New visitors follow the OS. An explicit choice, once made, is stored and
 *  stays authoritative — the OS never overrides it back. */
const DEFAULT_THEME: Theme = "system";
/** What a resolved read falls back to when nothing can be determined (no DOM,
 *  no matchMedia). Light, matching the pre-mount script in index.html. */
const DEFAULT_RESOLVED: ResolvedTheme = "light";

const DARK_SCHEME_QUERY = "(prefers-color-scheme: dark)";

function darkSchemeQuery(): MediaQueryList | null {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return null;
  return window.matchMedia(DARK_SCHEME_QUERY);
}

/** Read the persisted preference. Defaults to `"system"` when unset or
 *  invalid, and never throws if localStorage is unavailable. */
export function readThemePref(): Theme {
  try {
    const v = localStorage.getItem(PREF_KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    /* localStorage unavailable — fall through */
  }
  return DEFAULT_THEME;
}

/** Persist the chosen preference. No-ops if localStorage is unavailable. */
export function writeThemePref(theme: Theme): void {
  try {
    localStorage.setItem(PREF_KEY, theme);
  } catch {
    /* ignore */
  }
}

/** Collapse a preference to the theme to paint: an explicit choice passes
 *  through, `"system"` reads `prefers-color-scheme`. */
export function resolveTheme(pref: Theme): ResolvedTheme {
  if (pref !== "system") return pref;
  return darkSchemeQuery()?.matches ? "dark" : DEFAULT_RESOLVED;
}

/** Re-run `onChange` whenever the OS appearance flips. Returns the unsubscribe
 *  cleanup; a no-op subscription when matchMedia is unavailable. */
export function subscribeSystemTheme(onChange: () => void): () => void {
  const mql = darkSchemeQuery();
  if (!mql) return () => {};
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}

/** Apply a preference to the document by setting data-theme on <html> to its
 *  RESOLVED value, which the global.css `:root[data-theme="dark"]` block
 *  selects on. Also dispatches a `themechange` event so imperative consumers
 *  that can't recolor via the CSS cascade (the MapLibre layer hooks, which
 *  embed resolved hexes in style expressions) can rebuild — see
 *  useThemeSignal. Skips redundant writes if the resolved value is unchanged. */
export function applyTheme(pref: Theme): void {
  const theme = resolveTheme(pref);
  if (document.documentElement.dataset.theme !== theme) {
    document.documentElement.dataset.theme = theme;
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent<ResolvedTheme>(THEME_CHANGE_EVENT, { detail: theme }));
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

/** Snapshot of the painted theme, read from the DOM (`data-theme` on <html>) —
 *  the single source of truth applyTheme writes. Returns DEFAULT_RESOLVED
 *  (light) when the attribute is absent or unrecognized. Returns a stable
 *  primitive so useSyncExternalStore won't loop. Doubles as the server
 *  snapshot. */
function themeSnapshot(): ResolvedTheme {
  if (typeof document === "undefined") return DEFAULT_RESOLVED;
  const v = document.documentElement.dataset.theme;
  return v === "light" || v === "dark" ? v : DEFAULT_RESOLVED;
}

/** Painted theme as a re-render signal for imperative (non-CSS) consumers.
 *  A hook that puts this value in an effect's dependency array re-runs on a
 *  theme change — without every such component mounting its own useTheme().
 *  CSS consumers don't need this: the cascade recolors var(--*) references
 *  automatically.
 *
 *  Backed by useSyncExternalStore, which reads the snapshot at subscribe time
 *  (no missed-update window between the initial read and the listener
 *  attaching, unlike a useState + useEffect pair) and stays concurrent-safe. */
export function useThemeSignal(): ResolvedTheme {
  return useSyncExternalStore(subscribeThemeSignal, themeSnapshot, themeSnapshot);
}
