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

    // A reorder arriving before the last one has played leaves rows holding
    // an inverse transform. getBoundingClientRect reports the painted box,
    // so measuring now would read that offset as the row's real position
    // and animate from a place it was never at. Drop the pending releases
    // and the offsets they were going to clear, then measure.
    const pending = FLIP_FRAMES.get(container);
    if (pending?.length) {
      for (const id of pending) cancelAnimationFrame(id);
      for (const row of rows) {
        row.style.transition = "";
        row.style.transform = "";
      }
    }
    const frames: number[] = [];

    const previous = FLIP_POSITIONS.get(container);
    const current = new Map<string, number>();
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    // Every row is measured before any is offset: a read after a write forces
    // a fresh style pass, so interleaving them costs one per moved row.
    // Positions are taken against the list, not the viewport, so a scroll or
    // a reflow above the list between two runs is not read as a move.
    const moves: [HTMLElement, number][] = [];
    const origin = container.getBoundingClientRect().top;
    for (const row of rows) {
      const key = row.dataset.flipKey;
      if (key == null) continue;
      const top = row.getBoundingClientRect().top - origin;
      current.set(key, top);
      const before = previous?.get(key);
      if (before == null || reduced || before === top) continue;
      moves.push([row, before - top]);
    }
    for (const [row, dy] of moves) {
      row.style.transition = "none";
      row.style.transform = `translateY(${dy}px)`;
    }
    // One style pass with the offsets applied. A commit that runs in its own
    // task (a router transition, a query result) otherwise reaches the release
    // frame before any style was computed with the offset, so the browser sees
    // no change of transform, starts no transition, and the row jumps. A call,
    // not a bare property read, which the compiler drops as side-effect free.
    if (moves.length > 0) container.getBoundingClientRect();
    for (const [row] of moves) {
      frames.push(
        requestAnimationFrame(() => {
          row.style.transition = "";
          row.style.transform = "";
        }),
      );
    }
    FLIP_POSITIONS.set(container, current);
    FLIP_FRAMES.set(container, frames);
    return () => {
      for (const id of frames) cancelAnimationFrame(id);
    };
  }, [containerRef, signal]);
}

/** Keyed by the container element rather than held in a ref, so the measured
 *  positions live exactly as long as the DOM node they describe. */
const FLIP_POSITIONS = new WeakMap<HTMLElement, Map<string, number>>();

/** The releases a run scheduled, so the next run can cancel any that have
 *  not fired rather than letting them reset a row mid-animation. */
const FLIP_FRAMES = new WeakMap<HTMLElement, number[]>();
