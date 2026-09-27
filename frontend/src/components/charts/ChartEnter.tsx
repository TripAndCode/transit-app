import { useLayoutEffect, useRef, type CSSProperties, type RefObject } from "react";
import { prefersReducedMotion } from "../../utils/motion";

/**
 * Draws an SVG line (`<path>` or `<polyline>`) on over `var(--dur-4)`: reads
 * the rendered length via `getTotalLength()`, writes it as the `--len` custom
 * property, and adds the `.chart-draw-on` class -- which the stylesheet uses
 * to set `stroke-dasharray`/`stroke-dashoffset: var(--len)` and transition
 * the offset to 0. A second class (`.chart-draw-on--active`) is added one
 * frame later so the browser paints the "undrawn" state first; without that
 * frame there is nothing for the transition to animate from.
 *
 * No-ops entirely under `prefers-reduced-motion: reduce` (the line renders
 * fully drawn, immediately) and whenever the element can't report a length --
 * `getTotalLength()` is unimplemented in some test environments, and possibly
 * unavailable for a `display: none` element in a real browser. The draw-on
 * runs once per mount; a chart whose data changes in place does not replay
 * it (it is an entrance effect, not a per-update one).
 *
 * `--len` itself is re-measured whenever the line's geometry (`d` or
 * `points`) changes, though. The dasharray stays on for as long as the class
 * does, so a line that outgrew the length measured at mount -- a chart kept
 * mounted while its data changes -- would otherwise break into dash-length
 * segments with gaps between them. The attribute comparison comes first
 * because `getTotalLength()` forces a synchronous layout, and charts
 * re-render on every hover without changing their line.
 */
export function useDrawOn<T extends SVGGeometryElement>(ref: RefObject<T | null>): void {
  const measuredGeometry = useRef<string | null>(null);

  // Declared ahead of the entrance effect so that at mount, when the class is
  // not on yet, it leaves the one measurement to that effect.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el?.classList.contains("chart-draw-on")) return;
    const geometry = geometryOf(el);
    if (geometry === measuredGeometry.current) return;
    const length = measureLength(el);
    if (length === null) return;
    el.style.setProperty("--len", String(length));
    measuredGeometry.current = geometry;
  });

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || prefersReducedMotion()) return;
    const length = measureLength(el);
    if (length === null) return;

    measuredGeometry.current = geometryOf(el);
    el.style.setProperty("--len", String(length));
    el.classList.add("chart-draw-on");
    const raf = requestAnimationFrame(() => el.classList.add("chart-draw-on--active"));
    return () => cancelAnimationFrame(raf);
    // Runs once at mount -- see the docstring above. `ref` (from `useRef`) is
    // stable across renders, so listing it here satisfies exhaustive-deps
    // without changing when the effect re-runs.
  }, [ref]);
}

function geometryOf(el: SVGGeometryElement): string | null {
  return el.getAttribute("d") ?? el.getAttribute("points");
}

function measureLength(el: SVGGeometryElement): number | null {
  let length: number;
  try {
    length = el.getTotalLength();
  } catch {
    return null;
  }
  return Number.isFinite(length) && length > 0 ? length : null;
}

type StaggerOptions = {
  /** ms added per index. */
  step?: number;
  /** Maximum delay regardless of index, so a large grid finishes fading in
   *  within a bounded time instead of the last cell waiting on the whole
   *  count. */
  cap?: number;
};

/**
 * Inline `transitionDelay` for the Nth cell of a staggered-fade grid
 * (`HourlyHeatmap`, `DowBandGrid`). Pair with a class that starts a cell at
 * `opacity: 0` and transitions to `1` -- see `.chart-cell-enter` in
 * `global.css`, which (like every entrance animation here) only exists
 * inside the `prefers-reduced-motion: no-preference` block, so a
 * reduced-motion viewer never sees the cell start hidden.
 *
 * ```tsx
 * <rect className="chart-cell-enter" style={staggerDelay(index)} ... />
 * ```
 */
export function staggerDelay(index: number, { step = 6, cap = 900 }: StaggerOptions = {}): CSSProperties {
  return { transitionDelay: `${Math.min(index * step, cap)}ms` };
}
