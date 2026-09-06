import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  Map as MapIcon,
  BarChart3,
  LayoutDashboard,
  GitCompare,
  HelpCircle,
  History,
  ChevronLeft,
  ChevronRight,
  type LucideIcon,
} from "lucide-react";
import { onActivateKey } from "../../utils/a11y";
import { PREVIEW_AGENCIES, type PreviewAgencyKey } from "./previewData";

export type PreviewTabKey = "overview" | "map" | "analysis" | "network" | "live" | "ask";

type NavItem = { key: PreviewTabKey; labelKey: string; subtitleKey: string; Icon: LucideIcon };

// Same 5 tabs, in the same order, with the same labelKey/subtitleKey pairs
// and icons as the real signed-in sidebar's own `ITEMS`
// (components/Sidebar.tsx) -- Ask is deliberately excluded from this list
// (rendered as the dashed-border CTA below, matching Sidebar.tsx's own
// comment on why Ask isn't a peer tab).
const ITEMS: NavItem[] = [
  { key: "overview", labelKey: "nav.overview", subtitleKey: "nav.overview_subtitle", Icon: LayoutDashboard },
  { key: "map", labelKey: "nav.map", subtitleKey: "nav.map_subtitle", Icon: MapIcon },
  { key: "analysis", labelKey: "nav.analysis", subtitleKey: "nav.analysis_subtitle", Icon: BarChart3 },
  { key: "network", labelKey: "nav.network", subtitleKey: "nav.network_subtitle", Icon: GitCompare },
  { key: "live", labelKey: "nav.live", subtitleKey: "nav.live_subtitle", Icon: History },
];

// Same key as the real Sidebar.tsx's COLLAPSED_PREF_KEY -- intentionally
// shared, not reinvented, so toggling collapse here persists exactly like
// toggling it in the real signed-in sidebar (per this item's own spec).
const COLLAPSED_PREF_KEY = "transit.sidebarCollapsed";

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

function AgencyPickerMock({
  selectedKey,
  onSelect,
}: {
  selectedKey: PreviewAgencyKey;
  onSelect: (key: PreviewAgencyKey) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const current = PREVIEW_AGENCIES.find((a) => a.key === selectedKey) ?? PREVIEW_AGENCIES[0];

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        style={{
          width: "100%",
          background: "var(--bg-page)",
          color: "var(--text-primary)",
          border: "1px solid var(--border-subtle)",
          borderRadius: "var(--radius)",
          padding: "6px 10px",
          textAlign: "left",
          fontSize: 13,
          cursor: "pointer",
        }}
      >
        {t(current.nameKey)}
        <span style={{ float: "right", color: "var(--text-tertiary)" }}>▾</span>
      </button>
      {open && (
        <div
          role="listbox"
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            right: 0,
            zIndex: 3,
            background: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius)",
            boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
            overflow: "hidden",
          }}
        >
          {PREVIEW_AGENCIES.map((agency) => (
            <div
              key={agency.key}
              role="option"
              aria-selected={agency.key === selectedKey}
              tabIndex={0}
              onClick={() => {
                onSelect(agency.key);
                setOpen(false);
              }}
              onKeyDown={onActivateKey(() => {
                onSelect(agency.key);
                setOpen(false);
              })}
              style={{
                padding: "8px 10px",
                fontSize: 13,
                cursor: "pointer",
                background: agency.key === selectedKey ? "var(--accent-soft)" : "transparent",
              }}
            >
              {t(agency.nameKey)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** The dashboard-preview shell's sidebar: structurally the same
 *  brand-block / agency-picker / nav-list / Ask-CTA stack as the real
 *  `Sidebar.tsx`, collapsible to the same 64px rail via the same persisted
 *  preference, instead of item 63's flat horizontal explorer list. Every
 *  control here is wired to real state in `DashboardPreview`, not just
 *  styled to look clickable. */
export function PreviewSidebar({
  activeTab,
  onSelectTab,
  agencyKey,
  onSelectAgency,
}: {
  activeTab: PreviewTabKey;
  onSelectTab: (key: PreviewTabKey) => void;
  agencyKey: PreviewAgencyKey;
  onSelectAgency: (key: PreviewAgencyKey) => void;
}) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(readCollapsedPref);

  function toggleCollapsed() {
    setCollapsed((c) => {
      const next = !c;
      writeCollapsedPref(next);
      return next;
    });
  }

  const brand: ReactNode = (
    <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
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
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontWeight: 600,
            fontSize: 15,
            letterSpacing: "0.01em",
            color: "var(--text-primary)",
            whiteSpace: "nowrap",
          }}
        >
          {t("header.app_title")}
        </span>
      )}
    </div>
  );

  return (
    <aside
      style={{
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
        {brand}
        {!collapsed && (
          <button
            type="button"
            aria-label={t("nav.collapse_sidebar")}
            onClick={toggleCollapsed}
            style={{ background: "transparent", border: "none", color: "var(--text-tertiary)", cursor: "pointer", display: "flex", padding: 4 }}
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
          style={{ background: "transparent", border: "none", color: "var(--text-tertiary)", cursor: "pointer", display: "flex", justifyContent: "center", padding: "0 0 12px", width: "100%" }}
        >
          <ChevronRight size={16} strokeWidth={1.5} aria-hidden="true" />
        </button>
      )}

      {!collapsed && (
        <div style={{ padding: "0 22px 16px" }}>
          <AgencyPickerMock selectedKey={agencyKey} onSelect={onSelectAgency} />
        </div>
      )}

      <nav style={{ display: "flex", flexDirection: "column" }} aria-label={t("landing.preview.heading")}>
        {ITEMS.map((item) => {
          const isActive = item.key === activeTab;
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => onSelectTab(item.key)}
              title={collapsed ? t(item.labelKey) : undefined}
              aria-current={isActive ? "true" : undefined}
              style={{
                display: "flex",
                alignItems: collapsed ? "center" : "flex-start",
                justifyContent: collapsed ? "center" : "flex-start",
                gap: 12,
                padding: collapsed ? "12px 0" : "12px 22px",
                background: isActive ? "var(--accent-soft)" : "transparent",
                border: "none",
                borderLeft: `3px solid ${isActive ? "var(--accent)" : "transparent"}`,
                color: isActive ? "var(--accent)" : "var(--text-primary)",
                textAlign: "left",
                cursor: "pointer",
                width: "100%",
              }}
            >
              <item.Icon size={18} strokeWidth={1.5} aria-hidden="true" style={{ marginTop: collapsed ? 0 : 2, flexShrink: 0 }} />
              {!collapsed && (
                <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                  <span>{t(item.labelKey)}</span>
                  <span style={{ fontSize: 11, fontWeight: 400, color: "var(--text-tertiary)" }}>{t(item.subtitleKey)}</span>
                </span>
              )}
            </button>
          );
        })}
      </nav>

      <div style={{ flex: 1 }} />

      {/* Distinct CTA below the uniform nav list, matching the real
          Sidebar.tsx: Ask is deliberately not in ITEMS above so it reads as
          an action, not a peer tab. */}
      <button
        type="button"
        onClick={() => onSelectTab("ask")}
        title={collapsed ? t("nav.ask") : undefined}
        aria-current={activeTab === "ask" ? "true" : undefined}
        style={{
          margin: "8px 12px 0",
          padding: collapsed ? "10px 0" : "10px 12px",
          borderRadius: 7,
          display: "flex",
          alignItems: "center",
          justifyContent: collapsed ? "center" : "flex-start",
          gap: 9,
          color: activeTab === "ask" ? "var(--accent)" : "var(--text-secondary)",
          fontSize: 13,
          background: "transparent",
          border: `1px dashed ${activeTab === "ask" ? "var(--accent)" : "var(--border-soft)"}`,
          cursor: "pointer",
        }}
      >
        <HelpCircle size={16} strokeWidth={1.5} aria-hidden="true" />
        {!collapsed && t("nav.ask")}
      </button>
    </aside>
  );
}
