import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";
import { useLocation, useNavigationType } from "react-router-dom";

/**
 * Scroll position for the routed pane. The app scrolls inside that pane, not
 * the window, so the router's window-only `ScrollRestoration` never reaches
 * it. A new page opens at the top, a step back or forward returns to where
 * that history entry was left, and a change of query string alone (a filter
 * rewriting the URL) keeps the reader's place.
 *
 * Positions are filed under the history entry's key as the pane scrolls, not
 * on the way out: by the time a navigation commits, the outgoing page's
 * content is already gone and its scroll offset clamped to the new one.
 */
export function useRoutedPaneScroll(paneRef: RefObject<HTMLElement | null>): void {
  const { key, pathname } = useLocation();
  const navigationType = useNavigationType();
  const positions = useRef(new Map<string, number>());
  const entry = useRef<{ key: string; pathname: string } | null>(null);

  useLayoutEffect(() => {
    const pane = paneRef.current;
    const previous = entry.current;
    entry.current = { key, pathname };
    if (!pane) return;
    if (previous?.pathname !== pathname) {
      pane.scrollTop = navigationType === "POP" ? (positions.current.get(key) ?? 0) : 0;
    }
    positions.current.set(key, pane.scrollTop);
  }, [paneRef, key, pathname, navigationType]);

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
