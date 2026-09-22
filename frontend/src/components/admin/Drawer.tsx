import { useEffect, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import { Z_INDEX } from "../../styles/zIndex";

type DrawerProps = {
  open: boolean;
  onClose: () => void;
  /** Accessible name for the panel — an admin page can open different
   *  drawers over the same table, so "dialog" alone is not enough. */
  label: string;
  children: ReactNode;
};

/**
 * Right-hand detail panel, absolutely positioned inside the admin main area
 * so the list it belongs to stays on screen and keeps its scroll position.
 *
 * Deliberately not modal: the operator is meant to keep scanning rows (and
 * moving through them with `j`/`k`) while a row's detail is open, so the rest
 * of the page stays reachable and focus is not trapped. Escape closes, and
 * focus returns to whatever opened the panel.
 */
export function Drawer({ open, onClose, label, children }: DrawerProps) {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement;
    panelRef.current?.focus();
    return () => {
      if (opener instanceof HTMLElement && document.contains(opener)) opener.focus();
    };
  }, [open]);

  // Listened for on the document rather than the panel: the drawer is the
  // topmost transient surface while it is open, and focus may legitimately
  // sit outside it (the operator is still moving through the list behind).
  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      ref={panelRef}
      role="dialog"
      aria-label={label}
      tabIndex={-1}
      style={{
        position: "absolute",
        top: 0,
        right: 0,
        bottom: 0,
        width: "min(380px, 100%)",
        overflow: "auto",
        // `popover`, not the `drawer` rung: that one is paired with
        // `drawerBackdrop` for a surface that covers the page, and this one
        // deliberately does not. It is absolutely positioned inside the
        // admin main area, so it only has to outrank the list behind it.
        zIndex: Z_INDEX.popover,
        padding: "18px 20px",
        background: "var(--surface-1)",
        borderLeft: "1px solid var(--border-subtle)",
        outline: "none",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <h2 style={{ fontSize: 15, fontWeight: 700, margin: 0 }}>{label}</h2>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("admin.drawer.close")}
          style={{
            border: 0,
            background: "transparent",
            cursor: "pointer",
            color: "var(--text-secondary)",
            display: "inline-flex",
            padding: 4,
          }}
        >
          <X size={16} strokeWidth={1.8} aria-hidden="true" />
        </button>
      </div>
      <div style={{ marginTop: 12 }}>{children}</div>
    </div>
  );
}
