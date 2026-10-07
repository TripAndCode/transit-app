import { useEffect, useEffectEvent, type RefObject } from "react";
import { useTopmostEscape } from "./useFocusTrap";

/**
 * Dismissal for a popover that annotates the page rather than covering it:
 * Escape through the shared overlay stack, so only the topmost open surface
 * answers a keypress, plus a pointer-down anywhere outside `rootRef`. Traps
 * nothing -- Tab leaves the popover the way it leaves any other control.
 * `onClose` is read through an effect event so an inline closure does not
 * re-register the listeners on every render.
 *
 * `onClose` learns why the popover closed. Only Escape should move focus back
 * to the trigger: an outside pointer-down is already moving focus to what was
 * pressed, and refocusing a trigger scrolled out of view would scroll the page
 * under the press. A pointer-down on `triggerRef` is left to the trigger's own
 * toggle, which would otherwise reopen what the pointer-down just closed.
 */
export function usePopoverDismiss(
  open: boolean,
  rootRef: RefObject<HTMLElement | null>,
  onClose: (reason: "escape" | "outside") => void,
  triggerRef?: RefObject<HTMLElement | null>,
): void {
  useTopmostEscape(open, () => onClose("escape"));
  const closeOutside = useEffectEvent(() => onClose("outside"));

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      const root = rootRef.current;
      const target = event.target as Node;
      if (!root || root.contains(target) || triggerRef?.current?.contains(target)) return;
      closeOutside();
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open, rootRef, triggerRef]);
}
