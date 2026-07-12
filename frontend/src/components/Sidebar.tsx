import { Link, NavLink, useNavigate, useParams } from "react-router-dom";
import {
  Map as MapIcon,
  BarChart3,
  LayoutDashboard,
  GitCompare,
  HelpCircle,
  Clock,
  CircleSlash,
  SquareDashed,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { ctxToQueryString, useRangeContext } from "../api/rangeContext";
import { clearLastAgency } from "../api/lastAgency";

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
  const navigate = useNavigate();
  // Carry only the filter dimensions across tab switches — building from
  // ctx (not raw location.search) avoids dragging unrelated query keys
  // like ?admin=1 or report-specific params into every other tab.
  const [ctx] = useRangeContext();
  const filterQS = ctxToQueryString(ctx);
  const suffix = filterQS ? `?${filterQS}` : "";

  return (
    <aside
      style={{
        // 230, not 210 — the brand block's title ("遅延ダッシュボード") needs
        // ~135px alongside the 32px icon + gap; 210 wrapped it to two lines.
        width: 230,
        background: "var(--bg-surface)",
        borderRight: "1px solid var(--border-soft)",
        padding: "16px 0",
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        height: "100%",
      }}
    >
      <Link
        to="/"
        style={{
          textDecoration: "none",
          color: "var(--text-primary)",
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "0 22px 16px",
        }}
      >
        <span
          aria-hidden="true"
          style={{
            width: 32,
            height: 32,
            flexShrink: 0,
            borderRadius: 8,
            background: "var(--accent)",
            color: "#fff",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontWeight: 700,
            fontSize: 15,
          }}
        >
          {t("header.app_title").slice(0, 1)}
        </span>
        <span style={{ display: "flex", flexDirection: "column", lineHeight: 1.1 }}>
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontWeight: 600,
              fontSize: 15,
              letterSpacing: "0.01em",
            }}
          >
            {t("header.app_title")}
          </span>
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 10.5,
              color: "var(--text-tertiary)",
              marginTop: 2,
              letterSpacing: "0.04em",
            }}
          >
            {t("header.app_tagline")}
          </span>
        </span>
      </Link>
      {!agencyId ? null : (
        <>
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
          <div style={{ flex: 1 }} />
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
          {import.meta.env.DEV && (
            <div style={{ marginTop: 16 }}>
              <div
                style={{
                  padding: "0 22px",
                  marginBottom: 6,
                  fontSize: 10.5,
                  fontWeight: 600,
                  letterSpacing: "0.07em",
                  textTransform: "uppercase",
                  color: "var(--text-tertiary)",
                }}
              >
                {t("nav.prototype_section_label")}
              </div>
              <button
                type="button"
                onClick={() => {
                  clearLastAgency();
                  navigate("/");
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  width: "100%",
                  padding: "8px 22px",
                  fontSize: 12,
                  color: "var(--text-tertiary)",
                  background: "transparent",
                  border: "none",
                  textAlign: "left",
                  cursor: "pointer",
                }}
              >
                <Clock size={15} strokeWidth={1.5} aria-hidden="true" />
                {t("nav.prototype_onboarding")}
              </button>
              <NavLink
                to={`/agencies/${agencyId}/overview${suffix}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "8px 22px",
                  fontSize: 12,
                  color: "var(--text-tertiary)",
                  textDecoration: "none",
                }}
              >
                <CircleSlash size={15} strokeWidth={1.5} aria-hidden="true" />
                {t("nav.prototype_stale_feed")}
              </NavLink>
              <NavLink
                to={`/agencies/${agencyId}/overview?from=2030-01-01&to=2030-01-07`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "8px 22px",
                  fontSize: 12,
                  color: "var(--text-tertiary)",
                  textDecoration: "none",
                }}
              >
                <SquareDashed size={15} strokeWidth={1.5} aria-hidden="true" />
                {t("nav.prototype_no_data")}
              </NavLink>
            </div>
          )}
        </>
      )}
    </aside>
  );
}
