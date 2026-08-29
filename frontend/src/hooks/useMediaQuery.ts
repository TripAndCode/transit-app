import { useSyncExternalStore } from "react";

/** Shared mobile/desktop breakpoint — the single source of truth consumed by
 *  every component that needs to know which layout it's in (ThreadSidebar's
 *  and Sidebar's conditional desktop/mobile split, plus any inline `<style>`
 *  media query that wants to stay in sync with it). Previously each
 *  component hardcoded its own "640px" literal independently. */
export const MOBILE_BREAKPOINT_PX = 640;
export const MOBILE_BREAKPOINT_QUERY = `(max-width: ${MOBILE_BREAKPOINT_PX}px)`;

/**
 * Tracks a media query's match state so desktop/mobile component variants
 * can be conditionally rendered instead of both always mounting (the old
 * pattern toggled visibility only via a CSS `display` media query, doubling
 * DOM nodes/listeners at every viewport width). Built on
 * `useSyncExternalStore` rather than a `useState`+`useEffect` pair:
 * `matchMedia` is an external mutable source, so this avoids an extra
 * render-then-resync timer for a value that's already correct at first
 * render.
 */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    () => window.matchMedia(query).matches,
  );
}
