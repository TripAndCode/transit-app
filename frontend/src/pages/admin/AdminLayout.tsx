import { NavLink, Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Building2, Users, Activity, Workflow, LayoutDashboard, type LucideIcon } from "lucide-react";
import { useAdminUsers } from "../../api/admin";

type NavItem = { to: string; end?: boolean; labelKey: string; Icon: LucideIcon; badge?: "approvals" };

/** Grouped by what an operator is doing, not by which table backs the page:
 *  running the service, managing who can use it, governing it, reading about
 *  it. A group with no destinations yet is declared here but not rendered —
 *  a heading with nothing under it reads as a broken nav, and `governance`
 *  appears the moment the audit log, feature flags or Ask ops page adds its
 *  entry. */
const NAV_GROUPS: readonly { groupKey: string; items: readonly NavItem[] }[] = [
  {
    groupKey: "operations",
    items: [
      { to: "/admin", end: true, labelKey: "admin.nav.board", Icon: LayoutDashboard },
      { to: "/admin/agencies", labelKey: "admin.nav.agencies", Icon: Building2 },
      { to: "/admin/ops", labelKey: "admin.nav.ops", Icon: Activity },
    ],
  },
  {
    groupKey: "people",
    items: [{ to: "/admin/users", labelKey: "admin.nav.users", Icon: Users, badge: "approvals" }],
  },
  { groupKey: "governance", items: [] },
  {
    groupKey: "reference",
    items: [{ to: "/admin/architecture", labelKey: "admin.nav.architecture", Icon: Workflow }],
  },
];

/** Users who can sign in but cannot use the AI features yet — the one admin
 *  queue that builds up silently, so it gets a badge rather than waiting to
 *  be discovered on the users page. */
function useApprovalsWaiting(): number {
  // `total` counts every match, so this asks the server the question rather
  // than filtering a page of rows -- a page-limited list stops counting once
  // the table outgrows it, and the badge silently undercounts from then on.
  const { data } = useAdminUsers({ llmApproved: "false", suspended: "false", limit: 1 });
  return data?.total ?? 0;
}

export function AdminLayout() {
  const { t } = useTranslation();
  const approvals = useApprovalsWaiting();

  return (
    <div style={{ display: "flex", minHeight: "100%", flex: 1 }}>
      <nav
        aria-label={t("admin.nav.label")}
        style={{
          width: 190,
          borderRight: "1px solid var(--border-soft)",
          padding: "20px 0",
          flexShrink: 0,
        }}
      >
        {NAV_GROUPS.filter((group) => group.items.length > 0).map(({ groupKey, items }) => (
          <div key={groupKey}>
            <p
              style={{
                margin: "12px 0 4px",
                padding: "0 20px",
                fontSize: "var(--text-xs)",
                fontWeight: 600,
                letterSpacing: "0.1em",
                textTransform: "uppercase",
                color: "var(--text-tertiary)",
              }}
            >
              {t(`admin.nav.group.${groupKey}`)}
            </p>
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {items.map(({ to, end, labelKey, Icon, badge }) => {
                const count = badge === "approvals" ? approvals : 0;
                return (
                  <li key={to}>
                    <NavLink
                      to={to}
                      end={end}
                      style={({ isActive }) => ({
                        display: "flex",
                        alignItems: "center",
                        gap: 10,
                        padding: "9px 20px",
                        textDecoration: "none",
                        color: isActive ? "var(--accent)" : "var(--text-secondary)",
                        fontWeight: isActive ? 600 : 400,
                        fontSize: 14,
                        background: isActive ? "var(--accent-soft)" : "transparent",
                        borderLeft: `3px solid ${isActive ? "var(--accent)" : "transparent"}`,
                      })}
                    >
                      <Icon size={16} strokeWidth={1.6} aria-hidden="true" style={{ flexShrink: 0, opacity: 0.85 }} />
                      {t(labelKey)}
                      {count > 0 && (
                        <span
                          data-testid="nav-badge"
                          style={{
                            marginLeft: "auto",
                            fontSize: "var(--text-xs)",
                            fontWeight: 600,
                            borderRadius: 999,
                            padding: "0 7px",
                            background: "var(--surface-2)",
                            color: "var(--text-secondary)",
                          }}
                        >
                          {count}
                        </span>
                      )}
                    </NavLink>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
      {/* Positioned so a page's Drawer can pin itself to this area's right edge. */}
      <main style={{ flex: 1, minWidth: 0, position: "relative" }}>
        <Outlet />
      </main>
    </div>
  );
}
