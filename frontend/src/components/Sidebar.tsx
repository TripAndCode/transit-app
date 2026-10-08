import { useState, type CSSProperties, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { PendingNavLink } from "./navPending";
import { NavIndicator } from "./NavIndicator";
import {
  HelpCircle,
  Info,
  Clock,
  CircleSlash,
  SquareDashed,
  ChevronLeft,
  ChevronRight,
  MoreHorizontal,
  Shield,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { useIsAdmin } from "../api/useIsAdmin";
import { useScreenQuery, withQuery } from "../api/screenScope";
import { clearLastAgency } from "../api/lastAgency";
import { useRailAgencyId } from "../api/railAgency";
import { AgencyPicker } from "./AgencyPicker";
import { SidebarUserMenu } from "./SidebarUserMenu";
import { SettingsDrawer } from "./SettingsDrawer";
import { useMediaQuery, MOBILE_BREAKPOINT_QUERY } from "../hooks/useMediaQuery";
import { OverlayBase } from "./ui/OverlayBase";
import { Z_INDEX } from "../styles/zIndex";
import { prefetchRouteChunk } from "../routes/lazyTabs";
import { SIDEBAR_NAV_ITEMS } from "./sidebarNavItems";
import { RailTooltip } from "./RailTooltip";
import { SidebarLineMap } from "./SidebarLineMap";
import { SidebarTicket } from "./SidebarTicket";

type SidebarNavItem = (typeof SIDEBAR_NAV_ITEMS)[number];

const ITEMS: readonly SidebarNavItem[] = SIDEBAR_NAV_ITEMS;

/** On a phone the bottom bar holds Pulse, Routes, Live and Ask; the other
 *  destinations are reached from the More sheet, which keeps each tab's label
 *  on one line at phone widths. */
const TAB_BAR_DESTINATIONS: ReadonlySet<string> = new Set(["pulse", "routes", "live"]);
const TAB_BAR_ITEMS = ITEMS.filter((item) => TAB_BAR_DESTINATIONS.has(item.to));
const MORE_SHEET_ITEMS = ITEMS.filter((item) => !TAB_BAR_DESTINATIONS.has(item.to));

const COLLAPSED_PREF_KEY = "transit.sidebarCollapsed";

/** The bottom tab bar's fixed height below `BP.sm`. `global.css`'s
 *  `.app-main` rule reserves the identical 56px as bottom padding under the
 *  routed content, so the tab bar's fixed positioning sits below the last
 *  row of whatever the active tab renders instead of on top of it -- kept
 *  in sync by that rule's comment pointing back here, since a plain
 *  TS constant has no way to reach a separate stylesheet. */
const MOBILE_TABBAR_HEIGHT_PX = 56;

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

/** The More sheet's vertical padding; its sticky header offsets by the
 *  same amount. */
const MORE_SHEET_PAD_PX = 16;

/** The mobile "…" destination: a bottom sheet holding the agency picker and
 *  the account/settings controls that don't fit as one of the four tab bar
 *  slots.
 *
 *  Not the shared `Modal`: its two variants are a centred card and a
 *  full-height side drawer, and this is anchored to the bottom edge above
 *  the tab bar. It takes the scrim, the trap and the Escape/backdrop close
 *  straight from `OverlayBase` and contributes only the anchoring. */
function MoreSheet({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <OverlayBase
      open
      onClose={onClose}
      ariaLabel={title}
      zIndex={Z_INDEX.drawer}
      scrimZIndex={Z_INDEX.drawerBackdrop}
      className="more-sheet ov-modal"
      style={{
        position: "fixed",
        right: 0,
        bottom: MOBILE_TABBAR_HEIGHT_PX,
        left: 0,
        maxHeight: "70vh",
        background: "var(--bg-surface)",
        borderRadius: "var(--radius-xl) var(--radius-xl) 0 0",
        boxShadow: "var(--el-3)",
        display: "flex",
        flexDirection: "column",
        overflowY: "auto",
        padding: `${MORE_SHEET_PAD_PX}px 0`,
      }}
    >
      {children}
    </OverlayBase>
  );
}

/** The current destination's fill and accent bar are the shared
 *  NavIndicator behind the links; a link itself only changes colour. */
function railLinkStyle(collapsedFlag: boolean) {
  return ({ isActive }: { isActive: boolean }): CSSProperties => ({
    position: "relative",
    display: "flex",
    alignItems: collapsedFlag ? "center" : "flex-start",
    justifyContent: collapsedFlag ? "center" : "flex-start",
    gap: 12,
    padding: collapsedFlag ? "10px 0" : "10px 22px",
    color: isActive ? "var(--accent-strong)" : "var(--text-primary)",
    borderLeft: "3px solid transparent",
    textDecoration: "none",
    transition: "color var(--transition)",
  });
}

export function Sidebar() {
  const { t } = useTranslation();
  const agencyId = useRailAgencyId();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const isAdmin = useIsAdmin();
  // Each screen opens with its own last filters (see api/screenScope), so
  // nothing set on the screen being left follows the visitor elsewhere.
  const screenQuery = useScreenQuery();
  const screenHref = (screen: string) => withQuery(`/agencies/${agencyId}/${screen}`, agencyId ? screenQuery(agencyId, screen) : "");
  const [collapsed, setCollapsed] = useState(readCollapsedPref);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // Below 640px (the shared MOBILE_BREAKPOINT_QUERY) the desktop rail's
  // fixed 230/64px width would eat most of a ~390px phone screen, leaving
  // almost no room for tab content. isMobile renders only the active
  // variant rather than mounting both and hiding one with CSS `display`,
  // which would double the nav's DOM nodes and listeners at every width.
  // Pulse, Routes, Live and Ask live in the persistent tab bar on mobile;
  // only the "more" sheet is mounted on demand, so its contents cannot break
  // a single-match query while it is closed.
  const isMobile = useMediaQuery(MOBILE_BREAKPOINT_QUERY);
  // Below BP.sm the rail's nav links move into a persistent bottom tab bar
  // (thumb-reachable, and it gives the tab content back the width the rail
  // used to take); this sheet holds what doesn't fit in the tab slots --
  // the agency picker, the other destinations, the Other group and the
  // settings/account controls -- behind "…".
  const [moreOpen, setMoreOpen] = useState(false);

  function toggleCollapsed() {
    setCollapsed((c) => {
      const next = !c;
      writeCollapsedPref(next);
      return next;
    });
  }

  function openSettings() {
    setSettingsOpen(true);
    setMoreOpen(false);
  }

  // The destinations, the Other group, the dev-only prototype section, and
  // the account menu — everything below the brand block. Shared by the
  // desktop rail (collapsedFlag reflects the persisted rail preference) and
  // the mobile "more" sheet (always rendered expanded; onNavigate closes the
  // sheet after a link is followed, mirroring ThreadSidebar's onSelect-
  // closes-drawer UX). The rail draws the destinations and Ask as a line
  // map. `inSheet` lists only the destinations the bottom tab bar leaves
  // out, Ask included: repeating the bar's links here would put the same
  // links twice on screen at once.
  function renderNavAndFooter(collapsedFlag: boolean, onNavigate?: () => void, inSheet = false) {
    return (
      <>
        {/* On the phone sheet the account row leads: at the end it sat below
            the sheet's first screen. */}
        {inSheet && <SidebarUserMenu onOpenSettings={openSettings} />}
        {!collapsedFlag && (
          <div style={{ padding: inSheet ? "0 22px 16px" : "0 14px 14px" }}>
            {inSheet ? <AgencyPicker /> : <SidebarTicket />}
          </div>
        )}
        {agencyId && !inSheet && <SidebarLineMap collapsed={collapsedFlag} agencyId={agencyId} screenQuery={screenQuery} />}
        {agencyId && inSheet && (
          <nav aria-label={t("nav.destinations_label")} style={{ position: "relative", display: "flex", flexDirection: "column" }}>
            <NavIndicator axis="y" watch={pathname} />
            {MORE_SHEET_ITEMS.map((item) => (
              <PendingNavLink
                key={item.to}
                to={screenHref(item.to)}
                onMouseEnter={() => prefetchRouteChunk(item.to)}
                onFocus={() => prefetchRouteChunk(item.to)}
                onClick={() => onNavigate?.()}
                style={railLinkStyle(false)}
              >
                <item.Icon size={18} strokeWidth={1.5} aria-hidden="true" style={{ marginTop: 2, flexShrink: 0 }} />
                <span>{t(item.labelKey)}</span>
              </PendingNavLink>
            ))}
          </nav>
        )}
        <nav aria-label={t("nav.other")} style={{ display: "flex", flexDirection: "column", marginTop: 16 }}>
          {!collapsedFlag && (
            <div
              style={{
                padding: "0 22px",
                marginBottom: 4,
                fontSize: "var(--text-xs)",
                color: "var(--text-tertiary)",
                letterSpacing: "0.04em",
              }}
            >
              {t("nav.other")}
            </div>
          )}
          <RailTooltip collapsed={collapsedFlag} label={t("nav.help")}>
            <PendingNavLink
              to="/help"
              aria-label={collapsedFlag ? t("nav.help") : undefined}
              onClick={() => onNavigate?.()}
              style={railLinkStyle(collapsedFlag)}
            >
              <HelpCircle size={18} strokeWidth={1.5} aria-hidden="true" style={{ marginTop: collapsedFlag ? 0 : 2, flexShrink: 0 }} />
              {!collapsedFlag && <span>{t("nav.help")}</span>}
            </PendingNavLink>
          </RailTooltip>
          <RailTooltip collapsed={collapsedFlag} label={t("nav.about")}>
            <NavLink
              to="/welcome"
              aria-label={collapsedFlag ? t("nav.about") : undefined}
              onClick={() => onNavigate?.()}
              style={railLinkStyle(collapsedFlag)}
            >
              <Info size={18} strokeWidth={1.5} aria-hidden="true" style={{ marginTop: collapsedFlag ? 0 : 2, flexShrink: 0 }} />
              {!collapsedFlag && <span>{t("nav.about")}</span>}
            </NavLink>
          </RailTooltip>
          {isAdmin && (
            <RailTooltip collapsed={collapsedFlag} label={t("account.admin_link")}>
              <PendingNavLink
                to="/admin"
                aria-label={collapsedFlag ? t("account.admin_link") : undefined}
                onClick={() => onNavigate?.()}
                style={railLinkStyle(collapsedFlag)}
              >
                <Shield size={18} strokeWidth={1.5} aria-hidden="true" style={{ marginTop: collapsedFlag ? 0 : 2, flexShrink: 0 }} />
                {!collapsedFlag && <span>{t("account.admin_link")}</span>}
              </PendingNavLink>
            </RailTooltip>
          )}
        </nav>
        <div style={{ flex: 1 }} />
        {!agencyId ? null : (
          <>
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
                    fontSize: "var(--text-xs)",
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
                  to={screenHref("live")}
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
                  to={`/agencies/${agencyId}/pulse?from=2030-01-01&to=2030-01-07`}
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
        {!collapsedFlag && !inSheet && <SidebarUserMenu onOpenSettings={openSettings} />}
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
          color: "var(--on-accent)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontWeight: 700,
          fontSize: "var(--text-base)",
        }}
      >
        {t("header.app_title").slice(0, 1)}
      </span>
      {/* One line, never broken mid-word: the rail is too narrow for a
          two-line name and a tagline, which stay on the sign-in pages. */}
      {!collapsedFlag && (
        <span
          style={{
            minWidth: 0,
            fontFamily: "var(--font-display)",
            fontWeight: 700,
            fontSize: "var(--text-base)",
            letterSpacing: "0.01em",
            whiteSpace: "nowrap",
            wordBreak: "keep-all",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {t("header.app_title")}
        </span>
      )}
    </Link>
  );

  if (isMobile) {
    const tabItemStyle = ({ isActive }: { isActive: boolean }): CSSProperties => ({
      position: "relative",
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      gap: 3,
      flex: 1,
      minWidth: 0,
      padding: "6px 2px 2px",
      color: isActive ? "var(--accent)" : "var(--text-secondary)",
      fontSize: "var(--text-xs)",
      textDecoration: "none",
      transition: "color var(--transition)",
    });

    return (
      <>
        {/* Bottom tab bar: thumb-reachable and gives tab content back the
            width the 36px rail used to take. Fixed, not a flex sibling of
            <main> — App.tsx's content column already reserves space for it
            via .app-shell's bottom padding at this breakpoint, so it can
            float over everything the way a native app's tab bar does. */}
        <nav
          className="app-tabbar"
          aria-label={t("nav.mobile_tabbar_label")}
          style={{
            position: "fixed",
            right: 0,
            bottom: 0,
            left: 0,
            // Persistent chrome, so it stays under every overlay rung rather
            // than sharing `drawer` with the sheet it opens. Above the
            // drawer backdrop the tabs would stay tappable while MoreSheet
            // claims `aria-modal`, and a tab press would route away leaving
            // the backdrop and the focus trap mounted over the new page.
            zIndex: Z_INDEX.sticky,
            height: MOBILE_TABBAR_HEIGHT_PX,
            display: "flex",
            alignItems: "stretch",
            background: "var(--bg-surface)",
            borderTop: "1px solid var(--border-soft)",
            paddingBottom: "env(safe-area-inset-bottom)",
          }}
        >
          <NavIndicator axis="x" watch={pathname} />
          {agencyId &&
            TAB_BAR_ITEMS.map((item) => (
              <PendingNavLink spinner={false}
                key={item.to}
                to={screenHref(item.to)}
                onMouseEnter={() => prefetchRouteChunk(item.to)}
                onFocus={() => prefetchRouteChunk(item.to)}
                style={tabItemStyle}
              >
                <item.Icon size={20} strokeWidth={1.5} aria-hidden="true" />
                <span>{t(item.labelKey)}</span>
              </PendingNavLink>
            ))}
          {agencyId && (
            <PendingNavLink spinner={false}
              to={screenHref("ask")}
              onMouseEnter={() => prefetchRouteChunk("ask")}
              onFocus={() => prefetchRouteChunk("ask")}
              style={tabItemStyle}
            >
              <HelpCircle size={20} strokeWidth={1.5} aria-hidden="true" />
              <span>{t("nav.ask")}</span>
            </PendingNavLink>
          )}
          <button
            type="button"
            // `font` first: the shorthand would otherwise reset the label's
            // size back to the button's inherited one.
            style={{ font: "inherit", ...tabItemStyle({ isActive: false }), background: "transparent", border: "none", cursor: "pointer" }}
            aria-haspopup="dialog"
            aria-expanded={moreOpen}
            onClick={() => setMoreOpen(true)}
          >
            <MoreHorizontal size={20} strokeWidth={1.5} aria-hidden="true" />
            <span>{t("nav.more")}</span>
          </button>
        </nav>

        {moreOpen && (
          <MoreSheet onClose={() => setMoreOpen(false)} title={t("nav.more_menu_label")}>
            {/* Pinned, so the way out stays in reach however far the sheet
                scrolls. */}
            <div
              style={{
                // Pulled up over the sheet's top padding and pinned at its
                // edge, so nothing scrolls by above the header.
                position: "sticky",
                top: -MORE_SHEET_PAD_PX,
                zIndex: Z_INDEX.raised,
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 4,
                margin: `-${MORE_SHEET_PAD_PX}px 0 0`,
                padding: `${MORE_SHEET_PAD_PX}px 12px 16px 22px`,
                background: "var(--bg-surface)",
              }}
            >
              {brandBlock(false)}
              <button
                type="button"
                aria-label={t("nav.close_menu")}
                onClick={() => setMoreOpen(false)}
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
                <X size={18} strokeWidth={1.5} aria-hidden="true" />
              </button>
            </div>
            {renderNavAndFooter(false, () => setMoreOpen(false), true)}
          </MoreSheet>
        )}

        <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      </>
    );
  }

  return (
    <>
      <aside
        className="app-sidebar-desktop"
        style={{
          // The line map's badge column is centred 38px in at either width
          // (sidebarLineMap.css), so the collapsed rail is twice that. 248
          // fits a station's name and its chord hint beside the badge.
          width: collapsed ? 76 : 248,
          background: "var(--bg-surface)",
          borderRight: "1px solid var(--border-soft)",
          padding: "16px 0",
          flexShrink: 0,
          display: "flex",
          flexDirection: "column",
          height: "100%",
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

      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </>
  );
}
