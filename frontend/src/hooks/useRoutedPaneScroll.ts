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

/** How long a step back keeps waiting for its page to grow tall enough to
 *  hold the place it returns to. Long enough for content served from the
 *  query cache or a quick refetch to lay out; past it, a jump would move a
 *  page the reader has already started on. */
const RESTORE_WINDOW_MS = 3000;

/** Positions kept, newest first. A browser's back/forward list holds on the
 *  order of fifty entries, so an older position can never be stepped back to. */
export const MAX_POSITIONS = 100;

/** Any of these on the pane means the reader has taken over the scroll. */
const READER_SCROLL_EVENTS = ["wheel", "touchstart", "keydown", "pointerdown"] as const;

/** Re-applies `top` each time nodes are added to or removed from the pane,
 *  which is how a page's data arrives, until the content is tall enough to hold
 *  it, the reader scrolls, or the window runs out. The routed content overflows
 *  a wrapper sized to the pane, so no element's box grows with it for a
 *  ResizeObserver to report. Returns a function that gives up early. */
function settleScroll(pane: HTMLElement, top: number, onDone: () => void): () => void {
  const observer = new MutationObserver(() => {
    pane.scrollTop = top;
    if (Math.abs(pane.scrollTop - top) < 1) stop();
  });
  const timer = window.setTimeout(stop, RESTORE_WINDOW_MS);
  function stop() {
    observer.disconnect();
    window.clearTimeout(timer);
    for (const type of READER_SCROLL_EVENTS) pane.removeEventListener(type, stop);
    onDone();
  }
  observer.observe(pane, { childList: true, subtree: true });
  for (const type of READER_SCROLL_EVENTS) pane.addEventListener(type, stop, { passive: true });
  return stop;
}

/**
 * Scroll position for the routed pane. The app scrolls inside that pane, not
 * the window, so the router's window-only `ScrollRestoration` never reaches
 * it. A step back or forward returns to where that history entry was left; a
 * new `page` opens at the top; any other navigation that stays on the same
 * page (a filter rewriting the query string, an overlay opening over its
 * list) keeps the reader's place.
 *
 * Positions are filed under the history entry's key as the pane scrolls, not
 * on the way out: by the time a navigation commits, the outgoing page's
 * content is already gone and its scroll offset clamped to the new one. A
 * step back into a page whose content is still loading is clamped the same
 * way, so it keeps re-applying the place until the content can hold it.
 */
export function useRoutedPaneScroll(paneRef: RefObject<HTMLElement | null>, page: string): void {
  const { key } = useLocation();
  const navigationType = useNavigationType();
  const positions = useRef(new Map<string, number>());
  function file(entryKey: string, top: number) {
    const map = positions.current;
    map.delete(entryKey);
    map.set(entryKey, top);
    if (map.size > MAX_POSITIONS) map.delete(map.keys().next().value!);
  }
  const entry = useRef<{ key: string; page: string } | null>(null);
  const cancelRestore = useRef<(() => void) | null>(null);

  useLayoutEffect(() => {
    const pane = paneRef.current;
    const previous = entry.current;
    entry.current = { key, page };
    cancelRestore.current?.();
    if (!pane) return;
    const saved = positions.current.get(key);
    if (navigationType === "POP" && saved !== undefined) {
      pane.scrollTop = saved;
      if (Math.abs(pane.scrollTop - saved) >= 1) {
        const stop = settleScroll(pane, saved, () => {
          if (cancelRestore.current === stop) cancelRestore.current = null;
        });
        cancelRestore.current = stop;
        return;
      }
    } else if (previous?.page !== page) {
      pane.scrollTop = 0;
    }
    file(key, pane.scrollTop);
  }, [paneRef, key, page, navigationType]);

  useEffect(() => {
    const pane = paneRef.current;
    if (!pane) return;
    // A restore still settling owns the offset; the clamped values it passes
    // through are not where the reader left this entry.
    const remember = () => {
      if (entry.current && !cancelRestore.current) file(entry.current.key, pane.scrollTop);
    };
    pane.addEventListener("scroll", remember, { passive: true });
    return () => {
      pane.removeEventListener("scroll", remember);
      cancelRestore.current?.();
    };
  }, [paneRef]);
}
