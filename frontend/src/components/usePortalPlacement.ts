import { useEffectEvent, useLayoutEffect, type RefObject } from "react";
import { computeTooltipPosition, type TooltipPlacement } from "./tooltipPosition";

/** Keeps a portalled overlay placed against its trigger in the viewport while
 *  `open`, through resizes and scrolls. The position is written straight to
 *  the node instead of held in state: the measurement only places an element
 *  that is already mounted, a state round-trip would re-render the trigger
 *  for it, and a layout effect places it before it paints. `content` re-runs
 *  the placement when the overlay's size may have changed with it. */
export function usePortalPlacement(
  open: boolean,
  triggerRect: () => DOMRect | undefined,
  overlayRef: RefObject<HTMLElement | null>,
  placement: TooltipPlacement,
  content?: unknown,
): void {
  const measureTrigger = useEffectEvent(triggerRect);

  useLayoutEffect(() => {
    if (!open) return;
    function place() {
      const trigger = measureTrigger();
      const overlay = overlayRef.current;
      if (!trigger || !overlay) return;
      const pos = computeTooltipPosition(trigger, overlay.getBoundingClientRect(), placement, {
        width: window.innerWidth,
        height: window.innerHeight,
      });
      overlay.style.left = `${pos.left}px`;
      overlay.style.top = `${pos.top}px`;
      overlay.dataset.placement = pos.placement;
    }
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, placement, content, overlayRef]);
}
