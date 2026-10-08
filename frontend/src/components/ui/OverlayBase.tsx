import { useEffect, useRef, type CSSProperties, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { useFocusTrap, useTopmostEscape } from "../../hooks/useFocusTrap";
import { focusableIn } from "../../utils/focusable";
import "./ui.css";

type LabelProps = { labelledBy: string; ariaLabel?: undefined } | { ariaLabel: string; labelledBy?: undefined };

export type OverlayBaseProps = {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  /** Panel rung, from `Z_INDEX`. Omitted by an overlay whose panel already
   *  sits inside its own scrim's stacking context and has nothing to order
   *  itself against. */
  zIndex?: number;
  /** Scrim rung, from `Z_INDEX`. The ladder pairs each panel rung with a
   *  backdrop rung one below it, so a modal opened over a drawer layers
   *  above the whole drawer rather than between its panel and its scrim. */
  scrimZIndex?: number;
  /** A full-bleed wash that also closes the overlay when clicked. Off for an
   *  overlay that deliberately leaves the page behind usable. */
  scrim?: boolean;
  /** Layout for the scrim element, which doubles as the panel's positioning
   *  container (the palette centres its panel this way). */
  scrimClassName?: string;
  /** `false` keeps the page behind fully usable: no `aria-modal`, no focus
   *  trap, no scroll lock -- Escape still closes and focus still returns to
   *  the opener. A non-modal panel does not take the page over, so the
   *  caller is responsible for announcing it (an `aria-live` heading). */
  modal?: boolean;
  /** Render into `document.body`. Off for an overlay positioned against a
   *  page region rather than the viewport, which a portal would tear away
   *  from its containing block. */
  portal?: boolean;
  initialFocusRef?: RefObject<HTMLElement | null>;
  /** Where focus lands on open when no `initialFocusRef` is given.
   *  `"panel"` reads the dialog from its own top for a screen reader;
   *  `"first"` puts the user on the first control. */
  initialFocus?: "panel" | "first";
  className?: string;
  style?: CSSProperties;
} & LabelProps;

/**
 * The one overlay surface: portal, themed scrim, Escape and backdrop close,
 * focus placement and restoration, and an opt-in focus trap. Modal, the
 * admin Drawer, the mobile "more" sheet and the command palette all compose
 * it, so a fix to any of those behaviors lands in every overlay at once
 * instead of in whichever copy the fixer happened to be looking at.
 *
 * Only the entrance is animated, and only on the scrim: a panel carrying its
 * own entrance class (`.ov-modal`) would otherwise have that animation
 * replaced by this one, with the winner decided by stylesheet order. There
 * is no exit animation because every consumer unmounts the overlay the
 * moment it closes -- focus restoration and "the dialog is gone" both happen
 * synchronously on close, and a delayed unmount would make both observable
 * later than the close itself.
 */
export function OverlayBase({
  open,
  onClose,
  children,
  zIndex,
  scrimZIndex,
  scrim = true,
  scrimClassName,
  modal = true,
  portal = true,
  initialFocusRef,
  initialFocus = "first",
  className,
  style,
  labelledBy,
  ariaLabel,
}: OverlayBaseProps) {
  const panelRef = useRef<HTMLDivElement>(null);

  // The trap owns focus placement for the modal path, so it is told where to
  // put it rather than corrected afterwards: two effects both calling
  // `.focus()` agree only for as long as they keep running in declaration
  // order. `initialFocus="panel"` resolves to the panel, which carries
  // `tabIndex={-1}` for exactly this.
  useFocusTrap(open && modal, panelRef, onClose, initialFocusRef ?? (initialFocus === "panel" ? panelRef : undefined));

  // The non-modal path's own focus restore. Declared before the placement
  // effect below so it reads `document.activeElement` while it is still the
  // opener, not the panel that is about to take focus.
  useEffect(() => {
    if (!open || modal) return;
    const opener = document.activeElement;
    return () => {
      if (opener instanceof HTMLElement && document.contains(opener)) opener.focus();
    };
  }, [open, modal]);

  // Shares the trap stack even though it traps nothing: a modal overlay
  // opened over this one must be the only surface an Escape reaches.
  useTopmostEscape(open && !modal, onClose);

  // The non-modal path's own focus placement, since no trap runs to do it.
  useEffect(() => {
    if (!open || modal) return;
    const panel = panelRef.current;
    const fallback = panel && initialFocus === "first" ? (focusableIn(panel)[0] ?? panel) : panel;
    (initialFocusRef?.current ?? fallback)?.focus();
  }, [open, modal, initialFocus, initialFocusRef]);

  if (!open) return null;

  const panel = (
    <div
      ref={panelRef}
      role="dialog"
      aria-modal={modal || undefined}
      aria-labelledby={labelledBy}
      aria-label={ariaLabel}
      tabIndex={-1}
      className={["ui-overlay-panel", className].filter(Boolean).join(" ")}
      style={{ zIndex, ...style }}
    >
      {children}
    </div>
  );

  const tree = scrim ? (
    <div
      role="presentation"
      className={["ui-overlay-scrim", scrimClassName].filter(Boolean).join(" ")}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      style={{ position: "fixed", inset: 0, background: "var(--scrim)", zIndex: scrimZIndex }}
    >
      {panel}
    </div>
  ) : (
    panel
  );

  return portal ? createPortal(tree, document.body) : tree;
}
