import { useState, type ReactNode } from "react";
import { Link, NavLink, useNavigate, useParams } from "react-router-dom";
import {
  Map as MapIcon,
  BarChart3,
  LayoutDashboard,
  GitCompare,
  HelpCircle,
  Clock,
  History,
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
import { SidebarUserMenu } from "./SidebarUserMenu";
import { SettingsDrawer } from "./SettingsDrawer";

type Item = { to: string; labelKey: string; subtitleKey: string; Icon: LucideIcon };

const ITEMS: Item[] = [
  { to: "overview", labelKey: "nav.overview", subtitleKey: "nav.overview_subtitle", Icon: LayoutDashboard },
  { to: "map", labelKey: "nav.map", subtitleKey: "nav.map_subtitle", Icon: MapIcon },
  { to: "analysis", labelKey: "nav.analysis", subtitleKey: "nav.analysis_subtitle", Icon: BarChart3 },
  { to: "network", labelKey: "nav.network", subtitleKey: "nav.network_subtitle", Icon: GitCompare },
  { to: "live", labelKey: "nav.live", subtitleKey: "nav.live_subtitle", Icon: History },
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
  // Narrow-viewport drawer: the desktop rail (below) is CSS-hidden under
  // 640px (matching ThreadSidebar's breakpoint) since its fixed 230/64px
  // width would otherwise eat most of a ~390px phone screen, leaving almost
  // no room for tab content. The drawer body below is only mounted while
  // open — unlike ThreadSidebar's always-mounted mobile twin — so the
  // common case (drawer closed) doesn't duplicate every nav label/link in
  // the DOM and break single-match queries in tests or a11y tooling.
  const [mobileOpen, setMobileOpen] = useState(false);

  function toggleCollapsed() {
    setCollapsed((c) => {
      const next = !c;
      writeCollapsedPref(next);
      return next;
    });
  }

  function openSettings() {
    setSettingsOpen(true);
    setMobileOpen(false);
  }

  // Nav links, the Ask CTA, the dev-only prototype section, and the account
  // menu — everything below the brand block. Shared by the desktop rail
  // (collapsedFlag reflects the persisted rail preference) and the mobile
  // drawer (always rendered expanded; onNavigate closes the drawer after a
  // link is followed, mirroring ThreadSidebar's onSelect-closes-drawer UX).
  function renderNavAndFooter(collapsedFlag: boolean, onNavigate?: () => void) {
    return (
      <>
        {!collapsedFlag && (
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
                title={collapsedFlag ? t(item.labelKey) : undefined}
                onClick={() => onNavigate?.()}
                style={({ isActive }) => ({
                  display: "flex",
                  alignItems: collapsedFlag ? "center" : "flex-start",
                  justifyContent: collapsedFlag ? "center" : "flex-start",
                  gap: 12,
                  padding: collapsedFlag ? "12px 0" : "12px 22px",
                  color: isActive ? "var(--accent)" : "var(--text-primary)",
                  background: isActive ? "var(--accent-soft)" : "transparent",
                  borderLeft: `3px solid ${isActive ? "var(--accent)" : "transparent"}`,
                  textDecoration: "none",
                  transition: "background var(--transition)",
                })}
              >
                <item.Icon size={18} strokeWidth={1.5} aria-hidden="true" style={{ marginTop: collapsedFlag ? 0 : 2, flexShrink: 0 }} />
                {!collapsedFlag && (
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
        {!agencyId ? null : (
          <>
            {/* Distinct CTA below the uniform nav list, matching the artifact
                mockup's dashed-border Ask button — Ask is deliberately not in the
                ITEMS loop above so it reads as an action, not a peer tab. */}
            <NavLink
              to={`/agencies/${agencyId}/ask${suffix}`}
              title={collapsedFlag ? t("nav.ask") : undefined}
              onClick={() => onNavigate?.()}
              style={({ isActive }) => ({
                margin: "8px 12px 0",
                padding: collapsedFlag ? "10px 0" : "10px 12px",
                borderRadius: 7,
                display: "flex",
                alignItems: "center",
                justifyContent: collapsedFlag ? "center" : "flex-start",
                gap: 9,
                color: isActive ? "var(--accent)" : "var(--text-secondary)",
                fontSize: 13,
                border: `1px dashed ${isActive ? "var(--accent)" : "var(--border-soft)"}`,
                textDecoration: "none",
                transition: "all var(--transition)",
              })}
            >
              <HelpCircle size={16} strokeWidth={1.5} aria-hidden="true" />
              {!collapsedFlag && t("nav.ask")}
            </NavLink>
            {!collapsedFlag && import.meta.env.DEV && (
              <div style={{ marginTop: 12 }}>
                {/* Visually quarantined from the real account controls below
                    (SidebarUserMenu) — a divider plus reduced opacity, so a
                    dev-only debug link never sits flush against sign-in/
                    settings the way it used to. */}
                <div style={{ height: 1, background: "var(--border-soft)", margin: "0 14px 12px" }} />
                <div style={{ opacity: 0.75 }}>
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
                    onNavigate?.();
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
                  onClick={() => onNavigate?.()}
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
                  onClick={() => onNavigate?.()}
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
              </div>
            )}
          </>
        )}
        {!collapsedFlag && <SidebarUserMenu onOpenSettings={openSettings} />}
      </>
    );
  }

  const brandBlock = (collapsedFlag: boolean): ReactNode => (
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
      {!collapsedFlag && (
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
  );

  return (
    <>
      <aside
        className="app-sidebar-desktop"
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
          {brandBlock(collapsed)}
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
        {renderNavAndFooter(collapsed)}
      </aside>

      {/* Narrow-viewport nav: a slim persistent rail (not a floating fixed
          button) holding just the hamburger trigger, plus a slide-in drawer
          for the rest — same drawer pattern as ThreadSidebar's mobile thread
          list. A genuine flex-row sibling of <main> (36px wide, same idea as
          the desktop rail just narrower) rather than position:fixed keeps
          the trigger from floating on top of the sticky GuestPrompt banner
          or the Data-staleness/Feed-health banners that stack above the
          padded content in App.tsx — those already claim the page's actual
          top-left corner on some agencies/states. */}
      <div
        className="app-sidebar-mobile"
        style={{
          width: 36,
          height: "100%",
          flexShrink: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          paddingTop: 12,
          background: "var(--bg-surface)",
          borderRight: "1px solid var(--border-soft)",
        }}
      >
        <button
          type="button"
          onClick={() => setMobileOpen(true)}
          aria-label={t("nav.open_menu")}
          style={{
            background: "transparent",
            border: "none",
            color: "var(--text-secondary)",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 32,
            height: 32,
          }}
        >
          <span style={{ fontSize: 18, lineHeight: 1 }}>☰</span>
        </button>

        {mobileOpen && (
          <>
            <div
              onClick={() => setMobileOpen(false)}
              role="presentation"
              style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.3)", zIndex: 300 }}
            />
            <aside
              style={{
                position: "fixed",
                top: 0,
                left: 0,
                bottom: 0,
                width: 260,
                zIndex: 301,
                background: "var(--bg-surface)",
                borderRight: "1px solid var(--border-soft)",
                display: "flex",
                flexDirection: "column",
                overflowY: "auto",
                padding: "16px 0",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 4,
                  padding: "0 12px 16px 22px",
                }}
              >
                {brandBlock(false)}
                <button
                  type="button"
                  aria-label={t("common.close")}
                  onClick={() => setMobileOpen(false)}
                  style={{
                    background: "transparent",
                    border: "none",
                    color: "var(--text-tertiary)",
                    cursor: "pointer",
                    display: "flex",
                    padding: 4,
                    flexShrink: 0,
                    fontSize: 18,
                    lineHeight: 1,
                  }}
                >
                  ×
                </button>
              </div>
              {renderNavAndFooter(false, () => setMobileOpen(false))}
            </aside>
          </>
        )}
      </div>

      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />

      <style>{`
        @media (max-width: 640px) {
          .app-sidebar-desktop { display: none !important; }
        }
        @media (min-width: 641px) {
          .app-sidebar-mobile { display: none !important; }
        }
      `}</style>
    </>
  );
}
