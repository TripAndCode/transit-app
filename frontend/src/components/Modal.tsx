import { useEffect, useRef, type CSSProperties, type ReactNode, type RefObject } from "react";
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

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function focusableIn(panel: HTMLElement): HTMLElement[] {
  return Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

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
    zIndex: Z_INDEX.modal,
  },
};

/**
 * Shared accessible overlay: Escape and backdrop click both close, Tab is
 * trapped between the panel's first and last focusable descendants, focus
 * moves into the panel on open (to `initialFocusRef` when given) and back to
 * whatever was focused beforehand on close, and body scroll is locked while
 * open. Callers own the panel's visual size/position via `className`/`style`
 * layered on top of the `variant` base (centered card, or a full-height side
 * sheet for drawer-style overlays like the mobile nav and settings panel).
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
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;

    previouslyFocused.current = document.activeElement as HTMLElement | null;
    const toFocus = initialFocusRef?.current ?? panelRef.current;
    toFocus?.focus();

    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusable = focusableIn(panel);
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
  }, [open, onClose, initialFocusRef]);

  if (!open) return null;

  return (
    <div
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.3)", zIndex: Z_INDEX.modalBackdrop }}
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
