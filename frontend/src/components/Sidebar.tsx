import { NavLink, useParams } from "react-router-dom";
import { Map as MapIcon, MessageSquare, Activity, BarChart3, type LucideIcon } from "lucide-react";
import { ctxToQueryString, useRangeContext } from "../api/rangeContext";

type Item = { to: string; label: string; Icon: LucideIcon };

const ITEMS: Item[] = [
  { to: "map", label: "地図", Icon: MapIcon },
  { to: "ask", label: "質問", Icon: MessageSquare },
  { to: "live", label: "リアルタイム", Icon: Activity },
  { to: "reports", label: "レポート", Icon: BarChart3 },
];

export function Sidebar() {
  const { agencyId } = useParams();
  // Carry only the filter dimensions across tab switches — building from
  // ctx (not raw location.search) avoids dragging unrelated query keys
  // like ?admin=1 or report-specific params into every other tab.
  const [ctx] = useRangeContext();
  const filterQS = ctxToQueryString(ctx);
  const suffix = filterQS ? `?${filterQS}` : "";
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
            to={`/agencies/${agencyId}/${item.to}${suffix}`}
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
            <item.Icon size={18} strokeWidth={1.5} aria-hidden="true" />
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
