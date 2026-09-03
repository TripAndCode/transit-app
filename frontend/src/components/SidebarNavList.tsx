import type { CSSProperties, ReactNode } from "react";

type SidebarNavItem<K extends string | number = string> = {
  key: K;
  label: ReactNode;
};

type SidebarNavListProps<K extends string | number = string> = {
  items: SidebarNavItem<K>[];
  /** `null` before any item has an established "active" state (e.g. data
   *  hasn't loaded yet) -- no item renders as active in that case. */
  activeKey: K | null;
  onSelect: (key: K) => void;
  ariaLabel: string;
  /** Sidebar column width in px. Callers differ (220 for the admin
   *  architecture page, 240 for the help manual). */
  width: number;
  /** Extra style merged onto the `<nav>` element itself, e.g. the help
   *  manual's sticky positioning -- kept as a caller concern rather than
   *  baked in, since not every sidebar wants it. */
  navStyle?: CSSProperties;
};

/** Shared vertical sidebar nav list: one `<button>` per item, styled
 *  identically to whichever item is "active" via `activeKey`. Extracted from
 *  near-identical hand-rolled copies in `HelpPage.tsx` (manual section list)
 *  and `AdminArchitecturePage.tsx` (feature-doc list) -- same structure and
 *  byte-for-byte identical inline styles, differing only in what drives
 *  "active" and what a click does. */
export function SidebarNavList<K extends string | number = string>({
  items,
  activeKey,
  onSelect,
  ariaLabel,
  width,
  navStyle,
}: SidebarNavListProps<K>) {
  return (
    <nav aria-label={ariaLabel} style={{ width, flexShrink: 0, ...navStyle }}>
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {items.map((item) => {
          const isActive = item.key === activeKey;
          return (
            <li key={item.key}>
              <button
                type="button"
                onClick={() => onSelect(item.key)}
                aria-current={isActive ? "true" : undefined}
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  background: isActive ? "var(--accent-soft)" : "transparent",
                  border: "none",
                  borderLeft: `3px solid ${isActive ? "var(--accent)" : "transparent"}`,
                  color: "var(--text-primary)",
                  fontWeight: isActive ? 600 : 400,
                  fontSize: 13,
                  lineHeight: 1.4,
                  padding: "8px 12px",
                  cursor: "pointer",
                  borderRadius: "var(--radius)",
                }}
              >
                {item.label}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
