import { useState } from "react";

/**
 * The laid-out width of the element the returned ref is attached to, in CSS
 * pixels, kept current as it resizes. `null` until the element has a width,
 * so in an environment that does no layout it stays `null` and the caller
 * keeps its own default. Without ResizeObserver it keeps the first width read.
 *
 * The first width is read when the element attaches, inside the commit, so a
 * chart sized from it never paints a frame at its default width first. A
 * width of zero (the element or an ancestor hidden with `display: none`) is
 * ignored rather than reported, so a hidden panel keeps the size it last drew
 * at instead of collapsing.
 *
 * Measure a box whose width comes from its container, never one sized by
 * what it holds: a box that grows to fit a chart drawn at the measured width
 * would never report anything narrower.
 */
export function useElementWidth<T extends Element>(): [ref: (el: T | null) => (() => void) | undefined, width: number | null] {
  const [width, setWidth] = useState<number | null>(null);

  const ref = (el: T | null) => {
    if (!el) return undefined;
    const take = (next: number) => {
      if (next > 0) setWidth(next);
    };
    take(el.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(([entry]) => take(entry.contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  };

  return [ref, width];
}
