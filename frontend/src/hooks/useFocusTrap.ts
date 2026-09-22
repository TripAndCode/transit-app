import { useEffect, useRef, type RefObject } from "react";
import { focusableIn } from "../utils/focusable";

/**
 * Local focus-trap primitive for an overlay that has no shared `Modal` to
 * depend on yet: while `active`, focus moves into the container (its first
 * focusable descendant, or the container itself), Tab wraps between the
 * container's first and last focusable descendants, Escape invokes
 * `onEscape` instead of doing nothing, the page behind stops scrolling, and
 * focus returns to whatever was focused before activation once `active` goes
 * false again. The scroll lock belongs with the trap rather than with each
 * caller: an overlay that holds Tab inside itself but lets a wheel or a
 * touch-drag move the page underneath is the same escape by another input.
 *
 * Distinct from a dismiss-on-Escape-only overlay (`SettingsDrawer`,
 * `PeakHourModal`): those never trap Tab, so a keyboard user can tab straight
 * through them into the page behind. This is for the one caller that
 * genuinely covers the screen while active.
 *
 * Only the topmost trap reacts to a keypress. Several can be active at once
 * -- the map's sheet at "full" while the tab bar's more-menu opens over it --
 * and each listens on `document`, where `stopPropagation` does not reach a
 * sibling listener on the same target. Without the stack, one Escape would
 * dismiss every open overlay at once instead of the one on top.
 */
export function useFocusTrap(
  active: boolean,
  containerRef: RefObject<HTMLElement | null>,
  onEscape: () => void,
): void {
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;

    previouslyFocused.current = document.activeElement as HTMLElement | null;
    const [first] = focusableIn(container);
    (first ?? container).focus();

    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const token = Symbol("focus-trap");
    ACTIVE_TRAPS.push(token);

    function onKeyDown(e: KeyboardEvent) {
      if (ACTIVE_TRAPS[ACTIVE_TRAPS.length - 1] !== token) return;
      if (e.key === "Escape") {
        e.stopPropagation();
        onEscape();
        return;
      }
      if (e.key !== "Tab" || !container) return;
      const focusable = focusableIn(container);
      if (focusable.length === 0) {
        e.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      const at = ACTIVE_TRAPS.indexOf(token);
      if (at !== -1) ACTIVE_TRAPS.splice(at, 1);
      document.body.style.overflow = prevOverflow;
      previouslyFocused.current?.focus();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- container identity, not a dep the effect should re-run for
  }, [active, onEscape]);
}

/** Activation order, so the last entry is whichever trap is on top. Overlays
 *  stack in the order they open, so nothing has to know about anything else
 *  to find out whether it is the one a keypress belongs to. */
const ACTIVE_TRAPS: symbol[] = [];
