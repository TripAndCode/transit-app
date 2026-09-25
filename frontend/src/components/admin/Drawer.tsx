import { type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import { OverlayBase } from "../ui/OverlayBase";
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
 * focus returns to whatever opened the panel. That is `OverlayBase`'s
 * `modal={false}`, which also rules out the scrim and the portal: a wash
 * would cover the list this panel exists to sit beside, and `document.body`
 * is not the containing block it is positioned against.
 *
 * Opening moves focus to the panel, which is what actually tells a screen
 * reader a detail panel appeared. The heading is additionally a polite live
 * region, covering the case where focus has since moved back out to the list
 * and the panel is swapped to another row: that changes the heading's text
 * in place, which is the shape `aria-live` reliably announces. The region is
 * the heading alone, not the header row, so what is announced is the panel's
 * name rather than its name plus a close button.
 */
export function Drawer({ open, onClose, label, children }: DrawerProps) {
  const { t } = useTranslation();

  return (
    <OverlayBase
      open={open}
      onClose={onClose}
      ariaLabel={label}
      modal={false}
      scrim={false}
      portal={false}
      initialFocus="panel"
      // `popover`, not the `drawer` rung: that one is paired with
      // `drawerBackdrop` for a surface that covers the page, and this one
      // deliberately does not. It is absolutely positioned inside the
      // admin main area, so it only has to outrank the list behind it.
      zIndex={Z_INDEX.popover}
      style={{
        position: "absolute",
        top: 0,
        right: 0,
        bottom: 0,
        width: "min(380px, 100%)",
        overflow: "auto",
        padding: "18px 20px",
        background: "var(--surface-1)",
        borderLeft: "1px solid var(--border-subtle)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <h2 aria-live="polite" style={{ fontSize: 15, fontWeight: 700, margin: 0 }}>
          {label}
        </h2>
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
    </OverlayBase>
  );
}
