import type { CSSProperties } from "react";
import { Z_INDEX } from "../styles/zIndex";

type Align = "left" | "right";

/** The one `<table>` element style. The admin `DataTable` is the single
 *  exception: a sticky header needs `border-collapse: separate`, since a
 *  collapsed border belongs to the table rather than to the pinned cell and
 *  scrolls away from under it. */
export const SHARED_TABLE: CSSProperties = { width: "100%", borderCollapse: "collapse", fontSize: 13 };

/** The single header-cell style for every table in the app -- the admin
 *  `DataTable`, the report/panel tables and the focused-analysis tables all
 *  read from here, so a table does not read as a different kind of surface
 *  depending on which screen it happens to sit on.
 *
 *  `sticky` pins the header inside a scrolling table container. It carries
 *  its own opaque background because body rows scroll underneath it, and a
 *  transparent header would let them show through. */
export function th({
  width,
  align = "left",
  sticky = false,
}: { width?: number | string; align?: Align; sticky?: boolean } = {}): CSSProperties {
  return {
    padding: "8px 10px",
    textAlign: align,
    fontSize: "var(--text-xs)",
    fontWeight: 600,
    letterSpacing: "0.04em",
    textTransform: "uppercase",
    color: "var(--text-secondary)",
    borderBottom: "1px solid var(--border-subtle)",
    width,
    ...(sticky ? { position: "sticky" as const, top: 0, zIndex: Z_INDEX.raised, background: "var(--surface-1)" } : null),
  };
}

/** The single body-cell style for every table in the app. */
export function td({ align }: { align?: Align } = {}): CSSProperties {
  return {
    padding: "8px 10px",
    fontSize: 13,
    textAlign: align,
    borderBottom: "1px solid var(--surface-2)",
    verticalAlign: "middle",
  };
}
