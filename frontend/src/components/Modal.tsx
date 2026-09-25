import { useRef, type CSSProperties, type ReactNode, type RefObject } from "react";
import { useFocusTrap } from "../hooks/useFocusTrap";
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
    zIndex: Z_INDEX.modal,
  },
  drawer: {
    position: "fixed",
    top: 0,
    bottom: 0,
    background: "var(--bg-surface)",
    zIndex: Z_INDEX.drawer,
  },
};

/**
 * Shared accessible overlay: backdrop click closes, and `useFocusTrap`
 * supplies the dialog semantics -- Escape closes (the topmost surface only),
 * Tab is trapped inside the panel, focus moves into it on open (to
 * `initialFocusRef` when given) and back to whatever was focused beforehand
 * on close, and body scroll is locked while open. Callers own the panel's
 * visual size/position via `className`/`style` layered on top of the
 * `variant` base (centered card, or a full-height side sheet for
 * drawer-style overlays like the mobile nav and settings panel).
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
  const panelRef = useRef<HTMLDivElement>(null);

  // The panel itself is the default landing spot rather than its first
  // control: a dialog that announces its own label before its contents is
  // what a screen reader user needs to know what just opened.
  useFocusTrap(open, panelRef, onClose, initialFocusRef ?? panelRef);

  if (!open) return null;

  return (
    <div
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.3)",
        // Each variant sits on its own rung. The ladder separates drawer
        // (300/301) from modal (400/401) so a modal opened over a drawer
        // layers above it; pinning both to the modal rungs would leave DOM
        // order to decide, which is what the ladder exists to stop.
        zIndex: variant === "drawer" ? Z_INDEX.drawerBackdrop : Z_INDEX.modalBackdrop,
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-label={ariaLabel}
        tabIndex={-1}
        className={className}
        style={{ ...BASE_PANEL_STYLE[variant], outline: "none", ...style }}
      >
        {children}
      </div>
    </div>
  );
}
