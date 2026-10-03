import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactElement } from "react";
import { createPortal } from "react-dom";
import { computeTooltipPosition, type TooltipPlacement } from "./tooltipPosition";

/** Pointer dwell required before a tooltip appears. Short enough to feel
 *  immediate on a deliberate hover, long enough that a pointer crossing a
 *  row of icon buttons on its way somewhere else never flashes any of them. */
const HOVER_INTENT_MS = 150;

type Props = {
  /** Already-translated text. The tooltip is reinforcement, never a control's
   *  only accessible name — give an icon-only trigger its own `aria-label`. */
  label: string;
  /** Preferred side. Flipped automatically when it would leave the viewport. */
  placement?: TooltipPlacement;
  /** Exactly one element, focusable if the tooltip is to be reachable by
   *  keyboard. Its own `aria-describedby`, if it has one, is replaced while
   *  the tooltip is open. */
  children: ReactElement;
};

/**
 * Accessible hover/focus tooltip for a single trigger.
 *
 * The bubble renders in a portal at `position: fixed`, measured against the
 * trigger, rather than as a descendant of it: the controls that want a tooltip
 * here are icon-only ones inside clipping, absolutely-positioned containers
 * (map overlays, the collapsed nav rail), and a descendant bubble would be
 * positioned by — and clipped to — that container.
 *
 * The trigger is wrapped in a `display: contents` anchor rather than cloned
 * with extra props: the anchor generates no box, so an absolutely-positioned
 * trigger keeps the containing block it had, the trigger's own handlers are
 * untouched, and the listeners stay ordinary JSX props (a hand-merged prop
 * object handed to `cloneElement` reads as a render-time ref access to the
 * compiler).
 */
/** Whether a focus event belongs to this tooltip rather than to one nested
 *  inside it. React's focus events bubble, so an outer tooltip sees focus
 *  land on an inner tooltip's trigger and would open alongside it, putting
 *  two bubbles on screen and two `aria-describedby` targets on one path.
 *  The innermost anchor owns the event; every ancestor ignores it. */
function isOwnTrigger(anchor: HTMLElement | null, target: EventTarget | null): boolean {
  return anchor !== null && target instanceof Element && target.closest(".tooltip-anchor") === anchor;
}

export function Tooltip({ label, placement = "top", children }: Props) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLSpanElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const id = useId();

  useEffect(
    () => () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
    },
    [],
  );

  // Escape dismisses even when the tooltip was opened by hover and nothing in
  // the subtree holds focus, so a bubble can never sit over what it covers.
  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  // `aria-describedby` belongs on the trigger itself: the anchor around it
  // renders no box, and a screen reader would never associate the two.
  useEffect(() => {
    const trigger = anchorRef.current?.firstElementChild;
    if (!open || !(trigger instanceof HTMLElement)) return;
    trigger.setAttribute("aria-describedby", id);
    return () => trigger.removeAttribute("aria-describedby");
  }, [open, id]);

  // Position is written straight to the node instead of held in state: the
  // measurement only exists to place an element that is already mounted, and
  // a state round-trip would re-render the trigger for it.
  useLayoutEffect(() => {
    if (!open) return;
    function place() {
      const trigger = anchorRef.current?.firstElementChild;
      const tip = tipRef.current;
      if (!trigger || !tip) return;
      const pos = computeTooltipPosition(
        trigger.getBoundingClientRect(),
        tip.getBoundingClientRect(),
        placement,
        { width: window.innerWidth, height: window.innerHeight },
      );
      tip.style.left = `${pos.left}px`;
      tip.style.top = `${pos.top}px`;
      tip.dataset.placement = pos.placement;
    }
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, placement, label]);

  function cancelPending() {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }

  return (
    <>
      <span
        ref={anchorRef}
        className="tooltip-anchor"
        onMouseEnter={() => {
          cancelPending();
          timerRef.current = setTimeout(() => setOpen(true), HOVER_INTENT_MS);
        }}
        onMouseLeave={() => {
          cancelPending();
          setOpen(false);
        }}
        // Focus is already a deliberate act, so it skips the dwell delay.
        onFocus={(e) => {
          if (!isOwnTrigger(anchorRef.current, e.target)) return;
          cancelPending();
          setOpen(true);
        }}
        onBlur={(e) => {
          if (!isOwnTrigger(anchorRef.current, e.target)) return;
          cancelPending();
          setOpen(false);
        }}
      >
        {children}
      </span>
      {open &&
        createPortal(
          <div ref={tipRef} id={id} role="tooltip" className="tooltip" data-placement={placement}>
            {label}
          </div>,
          document.body,
        )}
    </>
  );
}
