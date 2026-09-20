import { useLayoutEffect, type RefObject } from "react";

/** FLIP re-order animation for a list whose rows are identified by a
 *  `data-flip-key` attribute.
 *
 *  Re-sorting a keyed list makes React move the existing DOM nodes, which is
 *  instant and therefore unreadable: the eye cannot follow a row that teleports.
 *  This measures each row before and after `signal` changes, applies the
 *  inverse offset with transitions off, and releases it on the next frame so
 *  the row's own `transition: transform` plays it back to its new place.
 *
 *  `signal` is whatever changes the order (the sort key, the data revision).
 *  A row with no previous position -- the first render, or a row that just
 *  appeared -- is left alone: there is nowhere for it to come from. */
export function useFlipRows(containerRef: RefObject<HTMLElement | null>, signal: string): void {
  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const rows = Array.from(container.querySelectorAll<HTMLElement>("[data-flip-key]"));
    const previous = FLIP_POSITIONS.get(container);
    const current = new Map<string, number>();
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    for (const row of rows) {
      const key = row.dataset.flipKey;
      if (key == null) continue;
      const top = row.getBoundingClientRect().top;
      current.set(key, top);
      const before = previous?.get(key);
      if (before == null || reduced) continue;
      const dy = before - top;
      if (dy === 0) continue;
      row.style.transition = "none";
      row.style.transform = `translateY(${dy}px)`;
      requestAnimationFrame(() => {
        row.style.transition = "";
        row.style.transform = "";
      });
    }
    FLIP_POSITIONS.set(container, current);
  }, [containerRef, signal]);
}

/** Keyed by the container element rather than held in a ref, so the measured
 *  positions live exactly as long as the DOM node they describe. */
const FLIP_POSITIONS = new WeakMap<HTMLElement, Map<string, number>>();
