import { NavLink, useParams } from "react-router-dom";
import { Map as MapIcon, BarChart3, LayoutDashboard, GitCompare, HelpCircle, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ctxToQueryString, useRangeContext } from "../api/rangeContext";

type Item = { to: string; labelKey: string; subtitleKey: string; Icon: LucideIcon };

const ITEMS: Item[] = [
  { to: "overview", labelKey: "nav.overview", subtitleKey: "nav.overview_subtitle", Icon: LayoutDashboard },
  { to: "map", labelKey: "nav.map", subtitleKey: "nav.map_subtitle", Icon: MapIcon },
  { to: "analysis", labelKey: "nav.analysis", subtitleKey: "nav.analysis_subtitle", Icon: BarChart3 },
  { to: "network", labelKey: "nav.network", subtitleKey: "nav.network_subtitle", Icon: GitCompare },
];

export function Sidebar() {
  const { t } = useTranslation();
  const { agencyId } = useParams();
  // Carry only the filter dimensions across tab switches — building from
  // ctx (not raw location.search) avoids dragging unrelated query keys
  // like ?admin=1 or report-specific params into every other tab.
  const [ctx] = useRangeContext();
  const filterQS = ctxToQueryString(ctx);
  const suffix = filterQS ? `?${filterQS}` : "";
  if (!agencyId) return <aside style={{ width: 210 }} />;

  return (
    <aside
      style={{
        width: 210,
        background: "var(--bg-surface)",
        borderRight: "1px solid var(--border-soft)",
        padding: "16px 0",
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <nav style={{ display: "flex", flexDirection: "column" }}>
        {ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={`/agencies/${agencyId}/${item.to}${suffix}`}
            style={({ isActive }) => ({
              display: "flex",
              alignItems: "flex-start",
              gap: 12,
              padding: "12px 22px",
              color: isActive ? "var(--accent)" : "var(--text-primary)",
              background: isActive ? "var(--accent-soft)" : "transparent",
              borderLeft: `3px solid ${isActive ? "var(--accent)" : "transparent"}`,
              textDecoration: "none",
              transition: "background var(--transition)",
            })}
          >
            <item.Icon size={18} strokeWidth={1.5} aria-hidden="true" style={{ marginTop: 2, flexShrink: 0 }} />
            <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <span>{t(item.labelKey)}</span>
              <span style={{ fontSize: 11, fontWeight: 400, color: "var(--text-tertiary)" }}>
                {t(item.subtitleKey)}
              </span>
            </span>
          </NavLink>
        ))}
      </nav>
      {/* Distinct CTA below the uniform nav list, matching the artifact
          mockup's dashed-border Ask button — Ask is deliberately not in the
          ITEMS loop above so it reads as an action, not a peer tab. */}
      <NavLink
        to={`/agencies/${agencyId}/ask${suffix}`}
        style={({ isActive }) => ({
          margin: "8px 12px 0",
          padding: "10px 12px",
          borderRadius: 7,
          display: "flex",
          alignItems: "center",
          gap: 9,
          color: isActive ? "var(--accent)" : "var(--text-secondary)",
          fontSize: 13,
          border: `1px dashed ${isActive ? "var(--accent)" : "var(--border-soft)"}`,
          textDecoration: "none",
          transition: "all var(--transition)",
        })}
      >
        <HelpCircle size={16} strokeWidth={1.5} aria-hidden="true" />
        {t("nav.ask")}
      </NavLink>
    </aside>
  );
}
