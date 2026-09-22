import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function focusableIn(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

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

    function onKeyDown(e: KeyboardEvent) {
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
      document.body.style.overflow = prevOverflow;
      previouslyFocused.current?.focus();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- container identity, not a dep the effect should re-run for
  }, [active, onEscape]);
}
