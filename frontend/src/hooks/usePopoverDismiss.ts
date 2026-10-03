import { useEffect, useEffectEvent, type RefObject } from "react";
import { useTopmostEscape } from "./useFocusTrap";

/**
 * Dismissal for a popover that annotates the page rather than covering it:
 * Escape through the shared overlay stack, so only the topmost open surface
 * answers a keypress, plus a pointer-down anywhere outside `rootRef`. Traps
 * nothing -- Tab leaves the popover the way it leaves any other control.
 * `onClose` is read through an effect event so an inline closure does not
 * re-register the listeners on every render.
 */
export function usePopoverDismiss(
  open: boolean,
  rootRef: RefObject<HTMLElement | null>,
  onClose: () => void,
): void {
  useTopmostEscape(open, onClose);
  const close = useEffectEvent(() => onClose());

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      const root = rootRef.current;
      if (root && !root.contains(event.target as Node)) close();
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open, rootRef]);
}
