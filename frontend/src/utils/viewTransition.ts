import type { CSSProperties } from "react";
import { prefersReducedMotion } from "./motion";

/** The one shared element in the app: a route's label travelling from its
 *  row into the dossier's title. Exactly one element may carry it in each
 *  snapshot, so the row assigns it on click and the title holds it while a
 *  route is on the page. */
export const ROUTE_TITLE_TRANSITION = "route-title";

type ViewTransitionDocument = Document & {
  startViewTransition?: (update: () => void | Promise<void>) => { finished: Promise<void> };
};

export function supportsViewTransition(): boolean {
  return typeof document !== "undefined"
    && typeof (document as ViewTransitionDocument).startViewTransition === "function"
    && !prefersReducedMotion();
}

/** Runs `update` inside a view transition when the engine offers one and
 *  the viewer has not asked for less motion; otherwise runs it directly.
 *  A throw from the API (a transition already running, a detached
 *  document) also falls through to the direct path -- the update is the
 *  point, the transition is decoration. */
export async function withViewTransition(update: () => void | Promise<void>): Promise<void> {
  if (!supportsViewTransition()) { await update(); return; }
  try {
    const transition = (document as ViewTransitionDocument).startViewTransition!(update);
    await transition.finished.catch(() => {});
  } catch {
    await update();
  }
}

export function routeTitleStyle(active: boolean): CSSProperties | undefined {
  return active ? ({ viewTransitionName: ROUTE_TITLE_TRANSITION } as CSSProperties) : undefined;
}

/** A click the anchor would handle as a same-tab navigation. Everything
 *  else (modifier keys, middle button, an already-handled event) stays the
 *  anchor's: open-in-new-tab must keep working. */
export function isPlainLeftClick(e: { button: number; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean; defaultPrevented: boolean }): boolean {
  return e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey && !e.defaultPrevented;
}
