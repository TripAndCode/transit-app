import type { CSSProperties } from "react";

/** The look of an in-page navigation strip (Reports' document views), kept
 *  in one place so a second strip cannot drift from it. */
export const SCREEN_STRIP_STYLE: CSSProperties = {
  display: "flex",
  gap: 2,
  borderBottom: "1px solid var(--border-soft)",
  overflowX: "auto",
  marginBottom: 12,
};

export function screenStripLinkStyle(active: boolean): CSSProperties {
  return {
    padding: "9px 12px 8px",
    fontSize: "var(--text-sm)",
    whiteSpace: "nowrap",
    textDecoration: "none",
    color: active ? "var(--text-primary)" : "var(--text-secondary)",
    fontWeight: active ? 500 : 400,
    borderBottom: `2px solid ${active ? "var(--accent)" : "transparent"}`,
  };
}
