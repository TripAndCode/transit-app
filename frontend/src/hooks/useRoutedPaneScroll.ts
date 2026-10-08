import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";
import { useLocation, useNavigationType, type UIMatch } from "react-router-dom";

/** A route whose `handle` says `overlay` draws over its parent, which stays
 *  mounted under it (a detail drawer over its list), so it is not a page of
 *  its own. */
type RouteHandle = { overlay?: boolean } | undefined;

/** The page the matched routes show: the pathname of the deepest match that
 *  is not an overlay. */
export function routedPage(matches: readonly UIMatch[]): string {
  for (let i = matches.length - 1; i >= 0; i--) {
    if (!(matches[i].handle as RouteHandle)?.overlay) return matches[i].pathname;
  }
  return "/";
}

/**
 * Scroll position for the routed pane. The app scrolls inside that pane, not
 * the window, so the router's window-only `ScrollRestoration` never reaches
 * it. A new `page` opens at the top, a step back or forward returns to where
 * that history entry was left, and a navigation that stays on the same page
 * (a filter rewriting the query string, an overlay opening over its list)
 * keeps the reader's place.
 *
 * Positions are filed under the history entry's key as the pane scrolls, not
 * on the way out: by the time a navigation commits, the outgoing page's
 * content is already gone and its scroll offset clamped to the new one.
 */
export function useRoutedPaneScroll(paneRef: RefObject<HTMLElement | null>, page: string): void {
  const { key } = useLocation();
  const navigationType = useNavigationType();
  const positions = useRef(new Map<string, number>());
  const entry = useRef<{ key: string; page: string } | null>(null);

  useLayoutEffect(() => {
    const pane = paneRef.current;
    const previous = entry.current;
    entry.current = { key, page };
    if (!pane) return;
    if (previous?.page !== page) {
      pane.scrollTop = navigationType === "POP" ? (positions.current.get(key) ?? 0) : 0;
    }
    positions.current.set(key, pane.scrollTop);
  }, [paneRef, key, page, navigationType]);

  useEffect(() => {
    const pane = paneRef.current;
    if (!pane) return;
    const remember = () => {
      if (entry.current) positions.current.set(entry.current.key, pane.scrollTop);
    };
    pane.addEventListener("scroll", remember, { passive: true });
    return () => pane.removeEventListener("scroll", remember);
  }, [paneRef]);
}
