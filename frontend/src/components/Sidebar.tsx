import { NavLink, useLocation, useParams } from "react-router-dom";

type Item = { to: string; label: string; icon: string };

const ITEMS: Item[] = [
  { to: "map", label: "地図", icon: "🗺" },
  { to: "ask", label: "質問", icon: "💬" },
  { to: "live", label: "リアルタイム", icon: "📊" },
  { to: "reports", label: "レポート", icon: "📋" },
];

export function Sidebar() {
  const { agencyId } = useParams();
  // Preserve filter state (?from=...&routes=...&dow=...) across tab switches.
  // Without this, every tab click reset the user's filters to defaults.
  const { search } = useLocation();
  if (!agencyId) return <aside style={{ width: 180 }} />;

  return (
    <aside
      style={{
        width: 180,
        background: "var(--bg-surface)",
        borderRight: "1px solid var(--border-soft)",
        padding: "16px 0",
        flexShrink: 0,
      }}
    >
      <nav style={{ display: "flex", flexDirection: "column" }}>
        {ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={`/agencies/${agencyId}/${item.to}${search}`}
            style={({ isActive }) => ({
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "10px 20px",
              color: isActive ? "var(--accent)" : "var(--text-primary)",
              background: isActive ? "var(--accent-soft)" : "transparent",
              borderLeft: `3px solid ${isActive ? "var(--accent)" : "transparent"}`,
              textDecoration: "none",
              transition: "background var(--transition)",
            })}
          >
            <span style={{ fontSize: 18 }} aria-hidden>{item.icon}</span>
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
