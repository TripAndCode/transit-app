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
 * The draw-on fires once per mount, on the first render where `ready` is true
 * -- the caller's "real data has arrived" signal. A chart that mounts empty
 * while its query resolves would otherwise spend its one draw on nothing, and
 * a chart whose data changes in place does not replay it (it is a first-data
 * effect, not a per-update one).
 *
 * No-ops entirely under `prefers-reduced-motion: reduce` (the line renders
 * fully drawn, immediately) and whenever the element can't report a length --
 * `getTotalLength()` is unimplemented in some test environments, and possibly
 * unavailable for a `display: none` element in a real browser.
 *
 * `--len` itself is re-measured whenever the line's geometry (`d` or
 * `points`) changes, though. The dasharray stays on for as long as the class
 * does, so a line that outgrew the length measured at its first draw -- a
 * chart kept mounted while its data changes -- would otherwise break into
 * dash-length segments with gaps between them. The attribute comparison comes
 * first because `getTotalLength()` forces a synchronous layout, and charts
 * re-render on every hover without changing their line.
 */
export function useDrawOn<T extends SVGGeometryElement>(ref: RefObject<T | null>, ready = true): void {
  const measuredGeometry = useRef<string | null>(null);
  const drawnRef = useRef(false);

  // Declared ahead of the draw effect so that on the first `ready` render,
  // when the class is not on yet, it leaves the one measurement to that effect.
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

  // Fires once, on the first render with real data (`ready`), never on a
  // mount that precedes it and never again on a later data change.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !ready || drawnRef.current || prefersReducedMotion()) return;
    const length = measureLength(el);
    if (length === null) return;
    drawnRef.current = true;
    measuredGeometry.current = geometryOf(el);
    el.style.setProperty("--len", String(length));
    el.classList.add("chart-draw-on");
    // Deliberately not cancelled on cleanup. This effect never re-arms once
    // `drawnRef` is set, so a cancelled frame -- StrictMode's replayed mount,
    // or `ready` dropping before the next frame -- would park the line at its
    // undrawn dashoffset for good. A late class add on a detached node is
    // harmless.
    requestAnimationFrame(() => el.classList.add("chart-draw-on--active"));
  }, [ref, ready]);
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
 * The default cap plus the cell's `--dur-2` fade equals `--dur-3`: the last
 * cell of any grid has settled inside the data-change budget, however many
 * cells the grid holds.
 *
 * ```tsx
 * <rect className="chart-cell-enter" style={staggerDelay(index)} ... />
 * ```
 */
export function staggerDelay(index: number, { step = 4, cap = 360 }: StaggerOptions = {}): CSSProperties {
  return { transitionDelay: `${Math.min(index * step, cap)}ms` };
}
