import type { CSSProperties, ReactNode } from "react";
import { NavIndicator } from "./NavIndicator";

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

/** Shared vertical sidebar nav list: one `<button>` per item, the one
 *  matching `activeKey` marked current. Its fill is one NavIndicator that
 *  glides between entries; the entry itself changes colour only, never
 *  weight, so a two-line title keeps its line breaks as the mark moves.
 *  Used by the help manual and the admin architecture page. */
export function SidebarNavList<K extends string | number = string>({
  items,
  activeKey,
  onSelect,
  ariaLabel,
  width,
  navStyle,
}: SidebarNavListProps<K>) {
  return (
    <nav aria-label={ariaLabel} style={{ position: "relative", width, flexShrink: 0, ...navStyle }}>
      <NavIndicator axis="y" current='[aria-current="true"]' watch={activeKey} className="nav-indicator--rounded" />
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
                  position: "relative",
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  background: "transparent",
                  border: "none",
                  borderLeft: "3px solid transparent",
                  color: isActive ? "var(--accent-strong)" : "var(--text-primary)",
                  fontWeight: 400,
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
