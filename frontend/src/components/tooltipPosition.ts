export type TooltipPlacement = "top" | "bottom" | "left" | "right";

/** Distance between the trigger's edge and the tooltip. */
const GAP = 8;

type Rect = { top: number; bottom: number; left: number; right: number; width: number; height: number };
type Size = { width: number; height: number };

const OPPOSITE: Record<TooltipPlacement, TooltipPlacement> = {
  top: "bottom",
  bottom: "top",
  left: "right",
  right: "left",
};

function fits(placement: TooltipPlacement, trigger: Rect, tip: Size, viewport: Size): boolean {
  switch (placement) {
    case "top":
      return trigger.top - GAP - tip.height >= 0;
    case "bottom":
      return trigger.bottom + GAP + tip.height <= viewport.height;
    case "left":
      return trigger.left - GAP - tip.width >= 0;
    case "right":
      return trigger.right + GAP + tip.width <= viewport.width;
  }
}

function clamp(value: number, min: number, max: number): number {
  // max < min when the tooltip is larger than the viewport allows; the low
  // edge wins there, so the text starts on screen rather than off it.
  return Math.max(min, Math.min(value, max));
}

/**
 * Viewport coordinates for a fixed-position tooltip, and the side it ended up
 * on. The requested side is kept whenever it fits; otherwise the opposite side
 * is used, and if neither fits the request stands (clamping still keeps the
 * tooltip on screen). The cross axis is clamped into the viewport so a tooltip
 * on an edge-hugging trigger stays readable.
 */
export function computeTooltipPosition(
  trigger: Rect,
  tip: Size,
  placement: TooltipPlacement,
  viewport: Size,
): { left: number; top: number; placement: TooltipPlacement } {
  const resolved =
    fits(placement, trigger, tip, viewport) || !fits(OPPOSITE[placement], trigger, tip, viewport)
      ? placement
      : OPPOSITE[placement];

  if (resolved === "top" || resolved === "bottom") {
    const centred = trigger.left + trigger.width / 2 - tip.width / 2;
    return {
      left: clamp(centred, GAP, viewport.width - tip.width - GAP),
      top: resolved === "top" ? trigger.top - GAP - tip.height : trigger.bottom + GAP,
      placement: resolved,
    };
  }

  const centred = trigger.top + trigger.height / 2 - tip.height / 2;
  return {
    left: resolved === "left" ? trigger.left - GAP - tip.width : trigger.right + GAP,
    top: clamp(centred, GAP, viewport.height - tip.height - GAP),
    placement: resolved,
  };
}

