import type { CSSProperties } from "react";

type PillSize = "sm" | "md";

const PADDING: Record<PillSize, string> = {
  sm: "4px 12px",
  md: "5px 12px",
};

/** Shared toggle-pill style used by the filter bars. `size` covers the one
 *  padding difference between TabFilterBar's (md) and FilterContextBar's
 *  (sm) pills. */
export function pill(active: boolean, size: PillSize = "md"): CSSProperties {
  return {
    background: active ? "var(--accent-soft)" : "var(--bg-surface)",
    color: active ? "var(--accent)" : "var(--text-secondary)",
    border: `1px solid ${active ? "var(--accent)" : "var(--border-soft)"}`,
    borderRadius: 999,
    padding: PADDING[size],
    fontSize: "var(--text-xs)",
    fontWeight: active ? 600 : 400,
    cursor: "pointer",
    transition: "all var(--transition)",
  };
}

/** Shared small-caps label above a group of filter pills. */
export const groupLabel: CSSProperties = {
  fontSize: "var(--text-xs)",
  color: "var(--text-tertiary)",
  letterSpacing: "0.05em",
  textTransform: "uppercase",
  marginBottom: 6,
  display: "block",
};
