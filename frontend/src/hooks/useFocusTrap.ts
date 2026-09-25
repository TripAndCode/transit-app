import { useEffect, useEffectEvent, useRef, type RefObject } from "react";
import { focusableIn } from "../utils/focusable";

/**
 * The app's one focus-trap primitive, behind every surface that behaves like
 * a dialog (`Modal`, the command palette, the map's full sheet, the mobile
 * more-sheet). While `active`, focus moves into the container
 * (`initialFocusRef` when given, else its first focusable descendant, else
 * the container itself), Tab stays inside it, Escape invokes `onEscape`
 * instead of doing nothing, the page behind stops scrolling, and focus
 * returns to whatever was focused before activation once `active` goes false
 * again. The scroll lock belongs with the trap rather than with each caller:
 * an overlay that holds Tab inside itself but lets a wheel or a touch-drag
 * move the page underneath is the same escape by another input.
 *
 * Tab also pulls focus back in from outside the container, not only at the
 * container's own edges: focus that started outside -- a click on the page
 * behind, or a container still hidden when the trap activated -- would
 * otherwise tab on through the page with the overlay still up.
 *
 * Distinct from a dismiss-on-Escape-only popover (the filter popover, the
 * export menu, the sidebar user menu): those deliberately never trap Tab,
 * because they annotate the page rather than cover it, and tabbing out of
 * one is a normal way to leave it. Trap only what a user is meant to finish
 * or cancel before touching the page again.
 *
 * Only the topmost trap reacts to a keypress. Several can be active at once
 * -- the map's sheet at "full" while the tab bar's more-menu opens over it,
 * a `Modal` over either -- and each listens on `document`, where
 * `stopPropagation` does not reach a sibling listener on the same target.
 * Without the stack, one Escape would dismiss every open overlay at once
 * instead of the one on top.
 *
 * `onEscape` is read through an effect event so that a caller passing an
 * inline closure does not re-run the trap on every render: re-running
 * re-captures the restore target -- by then the trap's own panel -- and drags
 * focus back to the panel's first control from wherever the user had moved it.
 */
export function useFocusTrap(
  active: boolean,
  containerRef: RefObject<HTMLElement | null>,
  onEscape: () => void,
  initialFocusRef?: RefObject<HTMLElement | null>,
): void {
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const escape = useEffectEvent(() => onEscape());

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;

    previouslyFocused.current = document.activeElement as HTMLElement | null;
    const [firstOnActivate] = focusableIn(container);
    (initialFocusRef?.current ?? firstOnActivate ?? container).focus();

    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const token = Symbol("focus-trap");
    ACTIVE_TRAPS.push(token);

    function onKeyDown(e: KeyboardEvent) {
      if (ACTIVE_TRAPS[ACTIVE_TRAPS.length - 1] !== token) return;
      if (e.key === "Escape") {
        e.stopPropagation();
        escape();
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
      const focused = document.activeElement;
      const outside = !container.contains(focused);
      if (e.shiftKey && (outside || focused === first)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (outside || focused === last)) {
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
  }, [active, containerRef, initialFocusRef]);
}

/** Activation order, so the last entry is whichever trap is on top. Overlays
 *  stack in the order they open, so nothing has to know about anything else
 *  to find out whether it is the one a keypress belongs to. */
const ACTIVE_TRAPS: symbol[] = [];
