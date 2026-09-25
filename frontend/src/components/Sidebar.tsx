import { useState, type CSSProperties, type ReactElement, type ReactNode } from "react";
import { Link, NavLink, useNavigate, useParams } from "react-router-dom";
import {
  HelpCircle,
  Clock,
  CircleSlash,
  SquareDashed,
  ChevronLeft,
  ChevronRight,
  MoreHorizontal,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { ctxToQueryString, useRangeContext } from "../api/rangeContext";
import { clearLastAgency } from "../api/lastAgency";
import { AgencyPicker } from "./AgencyPicker";
import { SidebarUserMenu } from "./SidebarUserMenu";
import { SettingsDrawer } from "./SettingsDrawer";
import { CompactDataStatus } from "./analysis/CompactDataStatus";
import { Tooltip } from "./Tooltip";
import { useMediaQuery, MOBILE_BREAKPOINT_QUERY } from "../hooks/useMediaQuery";
import { OverlayBase } from "./ui/OverlayBase";
import { Z_INDEX } from "../styles/zIndex";
import { prefetchRouteChunk } from "../routes/lazyTabs";
import { openCommandPalette } from "./commandPaletteEvents";


import { SIDEBAR_NAV_ITEMS } from "./sidebarNavItems";

type SidebarNavItem = (typeof SIDEBAR_NAV_ITEMS)[number];

const ITEMS: readonly SidebarNavItem[] = SIDEBAR_NAV_ITEMS;

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

/** Nav links only carry a tooltip while the rail is collapsed -- expanded,
 *  the label is already on screen and a bubble repeating it is noise. The
 *  same collapse also strips the visible text, so the link takes an
 *  `aria-label` there: the tooltip describes a control, it never names one. */
function RailTooltip({
  collapsed,
  label,
  children,
}: {
  collapsed: boolean;
  label: string;
  children: ReactElement;
}) {
  if (!collapsed) return children;
  return (
    <Tooltip label={label} placement="right">
      {children}
    </Tooltip>
  );
}

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
        padding: "16px 0",
      }}
    >
      {children}
    </OverlayBase>
  );
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
  // Below 640px (the shared MOBILE_BREAKPOINT_QUERY) the desktop rail's
  // fixed 230/64px width would eat most of a ~390px phone screen, leaving
  // almost no room for tab content. isMobile renders only the active
  // variant rather than mounting both and hiding one with CSS `display`,
  // which would double the nav's DOM nodes and listeners at every width.
  // The four destinations live in the persistent tab bar on mobile; only
  // the "more" sheet is mounted on demand, so its contents cannot break a
  // single-match query while it is closed.
  const isMobile = useMediaQuery(MOBILE_BREAKPOINT_QUERY);
  // Below BP.sm the rail's nav links move into a persistent bottom tab bar
  // (thumb-reachable, and it gives the tab content back the width the rail
  // used to take); this sheet holds what doesn't fit in four tab slots --
  // the agency picker and the settings/account controls -- behind "…".
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

  // Nav links, the Ask CTA, the dev-only prototype section, and the account
  // menu — everything below the brand block. Shared by the desktop rail
  // (collapsedFlag reflects the persisted rail preference) and the mobile
  // "more" sheet (always rendered expanded; onNavigate closes the sheet
  // after a link is followed, mirroring ThreadSidebar's onSelect-closes-
  // drawer UX). `includeNav` is false for the mobile sheet: those
  // destinations already live in the bottom tab bar, so repeating them here
  // would be the same four links twice on screen at once.
  function renderNavAndFooter(collapsedFlag: boolean, onNavigate?: () => void, includeNav = true) {
    return (
      <>
        {!collapsedFlag && (
          <div style={{ padding: "0 22px 16px" }}>
            <AgencyPicker />
          </div>
        )}
        {includeNav && agencyId && (
          <nav style={{ display: "flex", flexDirection: "column" }}>
            {ITEMS.map((item) => (
              <RailTooltip key={item.to} collapsed={collapsedFlag} label={t(item.labelKey)}>
                <NavLink
                  to={`/agencies/${agencyId}/${item.to}${suffix}`}
                  aria-label={collapsedFlag ? t(item.labelKey) : undefined}
                  onMouseEnter={() => prefetchRouteChunk(item.to)}
                  onFocus={() => prefetchRouteChunk(item.to)}
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
                    </span>
                  )}
                </NavLink>
              </RailTooltip>
            ))}
          </nav>
        )}
        <div style={{ flex: 1 }} />
        {!agencyId ? null : (
          <>
            {/* Distinct CTA below the uniform nav list, matching the artifact
                mockup's dashed-border Ask button — Ask is deliberately not in the
                ITEMS loop above so it reads as an action, not a peer tab. Also
                skipped on the mobile sheet: Ask is one of the four bottom tabs
                there. */}
            {includeNav && (
            <RailTooltip collapsed={collapsedFlag} label={t("nav.ask")}>
              <NavLink
                to={`/agencies/${agencyId}/ask${suffix}`}
                aria-label={collapsedFlag ? t("nav.ask") : undefined}
                data-tour="ask-nav"
                onMouseEnter={() => prefetchRouteChunk("ask")}
                onFocus={() => prefetchRouteChunk("ask")}
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
                  fontSize: "var(--text-sm)",
                  border: `1px dashed ${isActive ? "var(--accent)" : "var(--border-soft)"}`,
                  textDecoration: "none",
                  transition: "color var(--transition), border-color var(--transition)",
                })}
              >
                <HelpCircle size={16} strokeWidth={1.5} aria-hidden="true" />
                {!collapsedFlag && t("nav.ask")}
              </NavLink>
            </RailTooltip>
            )}
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
                  to={`/agencies/${agencyId}/operations${suffix}`}
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
                  to={`/agencies/${agencyId}/period-overview?from=2030-01-01&to=2030-01-07`}
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
        {!collapsedFlag && <CompactDataStatus />}
        {!collapsedFlag && <SidebarUserMenu onOpenSettings={openSettings} />}
        {/* Passive discoverability hint for the ⌘K command palette (mounted
            once in App.tsx, not here) — clicking it opens the palette via a
            window event rather than shared state, so this component doesn't
            need to know the palette's open/closed status. */}
        {!collapsedFlag && (
          <button
            type="button"
            onClick={() => openCommandPalette()}
            aria-label={t("palette.hint_aria")}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 8,
              width: "100%",
              marginTop: 4,
              padding: "6px 22px",
              background: "transparent",
              border: "none",
              cursor: "pointer",
              color: "var(--text-tertiary)",
              fontSize: "var(--text-xs)",
            }}
          >
            <span>{t("palette.hint")}</span>
            <span style={{ display: "flex", gap: 3 }}>
              {["⌘", "K"].map((k) => (
                <kbd
                  key={k}
                  style={{
                    fontSize: "var(--text-xs)",
                    border: "1px solid var(--border-subtle)",
                    borderBottomWidth: 2,
                    borderRadius: 4,
                    padding: "0 5px",
                    background: "var(--bg-soft)",
                    color: "var(--text-secondary)",
                    fontFamily: "inherit",
                  }}
                >
                  {k}
                </kbd>
              ))}
            </span>
          </button>
        )}
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
      {!collapsedFlag && (
        <span style={{ display: "flex", flexDirection: "column", lineHeight: 1.1, minWidth: 0 }}>
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontWeight: 600,
              fontSize: "var(--text-base)",
              letterSpacing: "0.01em",
            }}
          >
            {t("header.app_title")}
          </span>
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontSize: "var(--text-xs)",
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

  if (isMobile) {
    const tabItemStyle = ({ isActive }: { isActive: boolean }): CSSProperties => ({
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
          {agencyId &&
            ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={`/agencies/${agencyId}/${item.to}${suffix}`}
                onMouseEnter={() => prefetchRouteChunk(item.to)}
                onFocus={() => prefetchRouteChunk(item.to)}
                style={tabItemStyle}
              >
                <item.Icon size={20} strokeWidth={1.5} aria-hidden="true" />
                <span>{t(item.labelKey)}</span>
              </NavLink>
            ))}
          {agencyId && (
            <NavLink
              to={`/agencies/${agencyId}/ask${suffix}`}
              onMouseEnter={() => prefetchRouteChunk("ask")}
              onFocus={() => prefetchRouteChunk("ask")}
              style={tabItemStyle}
            >
              <HelpCircle size={20} strokeWidth={1.5} aria-hidden="true" />
              <span>{t("nav.ask")}</span>
            </NavLink>
          )}
          <button
            type="button"
            style={{ ...tabItemStyle({ isActive: false }), background: "transparent", border: "none", cursor: "pointer", font: "inherit" }}
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
            {renderNavAndFooter(false, () => setMoreOpen(false), false)}
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

      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </>
  );
}
