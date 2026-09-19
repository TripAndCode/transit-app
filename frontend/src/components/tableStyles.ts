import type { CSSProperties } from "react";

type Align = "left" | "right";

/** Shared header-cell style for the report/panel tables. */
export function th({ width, align = "left" }: { width?: number; align?: Align } = {}): CSSProperties {
  return {
    padding: "8px 10px",
    textAlign: align,
    fontWeight: 500,
    color: "var(--text-secondary)",
    fontSize: 12,
    width,
  };
}

/** Shared body-cell style for the report/panel tables. */
export function td({ align }: { align?: Align } = {}): CSSProperties {
  return {
    padding: "6px 10px",
    fontSize: 13,
    textAlign: align,
  };
}
