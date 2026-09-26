import { type CSSProperties, type ReactNode, type RefObject } from "react";
import { OverlayBase } from "./ui/OverlayBase";
import { Z_INDEX } from "../styles/zIndex";

type LabelProps = { labelledBy: string; ariaLabel?: undefined } | { ariaLabel: string; labelledBy?: undefined };

type Props = {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  initialFocusRef?: RefObject<HTMLElement | null>;
  /** "drawer" keeps the shared Escape/trap/restore/scroll-lock behavior but
   *  defaults the panel to a full-height side sheet instead of a centered
   *  card -- callers still position it left/right via `style`/`className`. */
  variant?: "modal" | "drawer";
  className?: string;
  style?: CSSProperties;
} & LabelProps;

const BASE_PANEL_STYLE: Record<"modal" | "drawer", CSSProperties> = {
  modal: {
    position: "fixed",
    top: "50%",
    left: "50%",
    transform: "translate(-50%, -50%)",
    background: "var(--bg-surface)",
  },
  drawer: {
    position: "fixed",
    top: 0,
    bottom: 0,
    background: "var(--bg-surface)",
  },
};

// Each variant sits on its own rung. The ladder separates drawer (300/301)
// from modal (400/401) so a modal opened over a drawer layers above it;
// pinning both to the modal rungs would leave DOM order to decide, which is
// what the ladder exists to stop.
const RUNGS: Record<"modal" | "drawer", { panel: number; scrim: number }> = {
  modal: { panel: Z_INDEX.modal, scrim: Z_INDEX.modalBackdrop },
  drawer: { panel: Z_INDEX.drawer, scrim: Z_INDEX.drawerBackdrop },
};

/**
 * The centered-card and side-sheet overlays, over the shared `OverlayBase`.
 * Callers own the panel's visual size/position via `className`/`style`
 * layered on top of the `variant` base; everything else -- scrim, Escape and
 * backdrop close, focus trap and restore, scroll lock -- belongs to the base.
 *
 * Focus lands on the panel itself rather than its first control (unless a
 * caller names one with `initialFocusRef`), so a screen reader reads the
 * dialog from its top instead of starting part-way through it.
 */
export function Modal({
  open,
  onClose,
  children,
  initialFocusRef,
  variant = "modal",
  className,
  style,
  labelledBy,
  ariaLabel,
}: Props) {
  const rungs = RUNGS[variant];
  // Re-narrowed rather than spread: the base takes the same either/or label
  // contract, and spreading both keys would hand it `ariaLabel: undefined`
  // alongside `labelledBy`, which the union does not admit.
  const label: LabelProps = labelledBy != null ? { labelledBy } : { ariaLabel: ariaLabel! };
  return (
    <OverlayBase
      open={open}
      onClose={onClose}
      zIndex={rungs.panel}
      scrimZIndex={rungs.scrim}
      initialFocus="panel"
      initialFocusRef={initialFocusRef}
      className={className}
      style={{ ...BASE_PANEL_STYLE[variant], ...style }}
      {...label}
    >
      {children}
    </OverlayBase>
  );
}
