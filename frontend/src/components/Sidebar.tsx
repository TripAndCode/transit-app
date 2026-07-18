import { useState } from "react";
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
  ChevronLeft,
  ChevronRight,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { ctxToQueryString, useRangeContext } from "../api/rangeContext";
import { clearLastAgency } from "../api/lastAgency";
import { AgencyPicker } from "./AgencyPicker";
import { HeaderUserMenu } from "./HeaderUserMenu";
import { LocaleToggle } from "./LocaleToggle";
import { ThemeToggle } from "./ThemeToggle";
import { SettingsDrawer } from "./SettingsDrawer";

type Item = { to: string; labelKey: string; subtitleKey: string; Icon: LucideIcon };

const ITEMS: Item[] = [
  { to: "overview", labelKey: "nav.overview", subtitleKey: "nav.overview_subtitle", Icon: LayoutDashboard },
  { to: "map", labelKey: "nav.map", subtitleKey: "nav.map_subtitle", Icon: MapIcon },
  { to: "analysis", labelKey: "nav.analysis", subtitleKey: "nav.analysis_subtitle", Icon: BarChart3 },
  { to: "network", labelKey: "nav.network", subtitleKey: "nav.network_subtitle", Icon: GitCompare },
];

const COLLAPSED_PREF_KEY = "transit.sidebarCollapsed";

/** Read the persisted collapse preference. No-ops to `false` (expanded) if
 *  localStorage is unavailable or unset — matches theme.ts's fail-open shape. */
function readCollapsedPref(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_PREF_KEY) === "1";
  } catch {
    return false;
  }
}

function writeCollapsedPref(collapsed: boolean): void {
  try {
    localStorage.setItem(COLLAPSED_PREF_KEY, collapsed ? "1" : "0");
  } catch {
    /* ignore */
  }
}

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
  const [collapsed, setCollapsed] = useState(readCollapsedPref);
  const [settingsOpen, setSettingsOpen] = useState(false);

  function toggleCollapsed() {
    setCollapsed((c) => {
      const next = !c;
      writeCollapsedPref(next);
      return next;
    });
  }

  return (
    <aside
      style={{
        // 230, not 210 — the brand block's title ("遅延ダッシュボード") needs
        // ~135px alongside the 32px icon + gap; 210 wrapped it to two lines.
        // Collapsed rail is 64: 32px icon + 16px padding each side.
        width: collapsed ? 64 : 230,
        background: "var(--bg-surface)",
        borderRight: "1px solid var(--border-soft)",
        padding: "16px 0",
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        height: "100%",
        transition: "width var(--transition)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: collapsed ? "center" : "space-between",
          gap: 4,
          padding: collapsed ? "0 0 16px" : "0 12px 16px 22px",
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
            minWidth: 0,
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
          {!collapsed && (
            <span style={{ display: "flex", flexDirection: "column", lineHeight: 1.1, minWidth: 0 }}>
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
          )}
        </Link>
        {!collapsed && (
          <button
            type="button"
            aria-label={t("nav.collapse_sidebar")}
            onClick={toggleCollapsed}
            style={{
              background: "transparent",
              border: "none",
              color: "var(--text-tertiary)",
              cursor: "pointer",
              display: "flex",
              padding: 4,
              flexShrink: 0,
            }}
          >
            <ChevronLeft size={16} strokeWidth={1.5} aria-hidden="true" />
          </button>
        )}
      </div>
      {collapsed && (
        <button
          type="button"
          aria-label={t("nav.expand_sidebar")}
          onClick={toggleCollapsed}
          style={{
            background: "transparent",
            border: "none",
            color: "var(--text-tertiary)",
            cursor: "pointer",
            display: "flex",
            justifyContent: "center",
            padding: "0 0 12px",
            width: "100%",
          }}
        >
          <ChevronRight size={16} strokeWidth={1.5} aria-hidden="true" />
        </button>
      )}
      {!collapsed && (
        <div style={{ padding: "0 22px 16px" }}>
          <AgencyPicker />
        </div>
      )}
      {agencyId && (
        <nav style={{ display: "flex", flexDirection: "column" }}>
          {ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={`/agencies/${agencyId}/${item.to}${suffix}`}
              title={collapsed ? t(item.labelKey) : undefined}
              style={({ isActive }) => ({
                display: "flex",
                alignItems: collapsed ? "center" : "flex-start",
                justifyContent: collapsed ? "center" : "flex-start",
                gap: 12,
                padding: collapsed ? "12px 0" : "12px 22px",
                color: isActive ? "var(--accent)" : "var(--text-primary)",
                background: isActive ? "var(--accent-soft)" : "transparent",
                borderLeft: `3px solid ${isActive ? "var(--accent)" : "transparent"}`,
                textDecoration: "none",
                transition: "background var(--transition)",
              })}
            >
              <item.Icon size={18} strokeWidth={1.5} aria-hidden="true" style={{ marginTop: collapsed ? 0 : 2, flexShrink: 0 }} />
              {!collapsed && (
                <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                  <span>{t(item.labelKey)}</span>
                  <span style={{ fontSize: 11, fontWeight: 400, color: "var(--text-tertiary)" }}>
                    {t(item.subtitleKey)}
                  </span>
                </span>
              )}
            </NavLink>
          ))}
        </nav>
      )}
      <div style={{ flex: 1 }} />
      {/* Global controls (agency-independent), moved in from the standalone
          top Header — Header rendered these unconditionally regardless of
          agency context (e.g. on /me, /admin, the onboarding page), so they
          stay ungated here too, unlike the nav/Ask/prototype blocks below. */}
      {!collapsed && (
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, padding: "0 12px 12px" }}>
          {agencyId && (
            <NavLink
              to={`/agencies/${agencyId}/live${suffix}`}
              style={({ isActive }) => ({
                fontSize: 12,
                textDecoration: "none",
                color: isActive ? "var(--accent)" : "var(--text-secondary)",
                fontWeight: isActive ? 600 : 400,
              })}
            >
              {t("nav.live")}
            </NavLink>
          )}
          <HeaderUserMenu />
          <LocaleToggle />
          <ThemeToggle />
          <button
            type="button"
            onClick={() => setSettingsOpen(true)}
            aria-label={t("header.settings_aria")}
            style={{
              background: "transparent",
              color: "var(--text-primary)",
              border: "1px solid var(--border-subtle)",
              padding: "6px 10px",
              borderRadius: 4,
              cursor: "pointer",
            }}
          >
            ⚙
          </button>
        </div>
      )}
      {!agencyId ? null : (
        <>
          {/* Distinct CTA below the uniform nav list, matching the artifact
              mockup's dashed-border Ask button — Ask is deliberately not in the
              ITEMS loop above so it reads as an action, not a peer tab. */}
          <NavLink
            to={`/agencies/${agencyId}/ask${suffix}`}
            title={collapsed ? t("nav.ask") : undefined}
            style={({ isActive }) => ({
              margin: "8px 12px 0",
              padding: collapsed ? "10px 0" : "10px 12px",
              borderRadius: 7,
              display: "flex",
              alignItems: "center",
              justifyContent: collapsed ? "center" : "flex-start",
              gap: 9,
              color: isActive ? "var(--accent)" : "var(--text-secondary)",
              fontSize: 13,
              border: `1px dashed ${isActive ? "var(--accent)" : "var(--border-soft)"}`,
              textDecoration: "none",
              transition: "all var(--transition)",
            })}
          >
            <HelpCircle size={16} strokeWidth={1.5} aria-hidden="true" />
            {!collapsed && t("nav.ask")}
          </NavLink>
          {!collapsed && import.meta.env.DEV && (
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
      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </aside>
  );
}
