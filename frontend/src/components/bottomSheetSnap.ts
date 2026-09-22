export type SnapPoint = "peek" | "half" | "full";

export const SNAP_ORDER: readonly SnapPoint[] = ["peek", "half", "full"];

/** How much of the sheet's own height each snap point reveals -- kept in one
 *  place so the drag math (`BottomSheet`'s `ratioFromClientY`) and the
 *  resting CSS heights agree with each other. Not an even 14/51/88 split:
 *  "half" sits closer to "peek" than the mockup's midpoint suggests, so the
 *  queue's first few rows are readable without giving up much map. */
export const SNAP_HEIGHT_VH: Record<SnapPoint, number> = { peek: 14, half: 45, full: 88 };

/** `position`'s 0..1 scale (see `nextSnap`) is normalized against this same
 *  peek-to-full range, not evenly spaced by index -- derived from
 *  `SNAP_HEIGHT_VH` rather than hardcoded so a drag that starts exactly at
 *  rest (`BottomSheet` seeds `dragRatio` from this table) reproduces that
 *  same resting height instead of jumping to an evenly-spaced one. */
export const SNAP_RATIO: Record<SnapPoint, number> = Object.fromEntries(
  SNAP_ORDER.map((point) => [
    point,
    (SNAP_HEIGHT_VH[point] - SNAP_HEIGHT_VH.peek) / (SNAP_HEIGHT_VH.full - SNAP_HEIGHT_VH.peek),
  ]),
) as Record<SnapPoint, number>;

/** A fast flick, in the drag's own normalized ratio-per-*second* units,
 *  strong enough to move the sheet one snap point regardless of where the
 *  pointer let go. Below this, the release just settles on whichever snap
 *  point the current position is closest to.
 *
 *  Per second, not per millisecond: at this scale a threshold has to be a
 *  speed a thumb can actually reach. 0.6 means crossing 60% of the
 *  peek-to-full travel in a second -- brisk for a deliberate flick, far
 *  above a drag that is being positioned by hand. The same number read as
 *  ratio-per-millisecond would demand the whole range in under two
 *  milliseconds, which no gesture reaches. */
const FLING_VELOCITY = 0.6;

export function clampRatio(ratio: number): number {
  return Math.min(1, Math.max(0, ratio));
}

function nearestSnap(ratio: number): SnapPoint {
  let best: SnapPoint = "peek";
  let bestDistance = Infinity;
  for (const point of SNAP_ORDER) {
    const distance = Math.abs(SNAP_RATIO[point] - ratio);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = point;
    }
  }
  return best;
}

/**
 * Pure snap-resolution for a pointer release: `position` is how expanded the
 * sheet currently is (0 = fully at "peek", 1 = fully at "full"), `velocity`
 * is the release velocity in the same 0..1 units per millisecond (negative
 * while dragging up/toward "full", positive while dragging down/toward
 * "peek"). A release below the fling threshold settles on the nearest snap
 * point to `position`; a fast fling instead moves exactly one snap point
 * from that nearest point, in the fling's direction, clamped to the ends of
 * the scale.
 */
export function nextSnap(position: number, velocity: number): SnapPoint {
  const nearest = nearestSnap(clampRatio(position));
  const idx = SNAP_ORDER.indexOf(nearest);
  if (velocity <= -FLING_VELOCITY) return SNAP_ORDER[Math.min(idx + 1, SNAP_ORDER.length - 1)];
  if (velocity >= FLING_VELOCITY) return SNAP_ORDER[Math.max(idx - 1, 0)];
  return nearest;
}
