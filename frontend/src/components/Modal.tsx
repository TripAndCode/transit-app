import { type CSSProperties, type ReactNode, type RefObject } from "react";
import { OverlayBase } from "./ui/OverlayBase";
import { Z_INDEX } from "../styles/zIndex";

type LabelProps = { labelledBy: string; ariaLabel?: undefined } | { ariaLabel: string; labelledBy?: undefined };

type Props = {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  initialFocusRef?: RefObject<HTMLElement | null>;
  className?: string;
  style?: CSSProperties;
} & LabelProps;

const BASE_PANEL_STYLE: CSSProperties = {
  position: "fixed",
  top: "50%",
  left: "50%",
  transform: "translate(-50%, -50%)",
  background: "var(--bg-surface)",
};

/**
 * The centered-card overlay, over the shared `OverlayBase`. Callers own the
 * panel's visual size/position via `className`/`style` layered on top of the
 * centered base; everything else -- scrim, Escape and backdrop close, focus
 * trap and restore, scroll lock -- belongs to the base.
 *
 * It sits on the modal rungs, above the drawer rungs the mobile sheet uses, so
 * a modal opened over that sheet layers above it rather than leaving DOM
 * order to decide.
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
  className,
  style,
  labelledBy,
  ariaLabel,
}: Props) {
  // Re-narrowed rather than spread: the base takes the same either/or label
  // contract, and spreading both keys would hand it `ariaLabel: undefined`
  // alongside `labelledBy`, which the union does not admit.
  const label: LabelProps = labelledBy != null ? { labelledBy } : { ariaLabel: ariaLabel! };
  return (
    <OverlayBase
      open={open}
      onClose={onClose}
      zIndex={Z_INDEX.modal}
      scrimZIndex={Z_INDEX.modalBackdrop}
      initialFocus="panel"
      initialFocusRef={initialFocusRef}
      className={className}
      style={{ ...BASE_PANEL_STYLE, ...style }}
      {...label}
    >
      {children}
    </OverlayBase>
  );
}
