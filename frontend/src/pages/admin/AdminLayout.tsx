import { NavLink, Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Building2, Users, Activity, type LucideIcon } from "lucide-react";

const NAV_ITEMS: readonly { to: string; labelKey: string; Icon: LucideIcon }[] = [
  { to: "/admin/agencies", labelKey: "admin.nav.agencies", Icon: Building2 },
  { to: "/admin/users", labelKey: "admin.nav.users", Icon: Users },
  { to: "/admin/ops", labelKey: "admin.nav.ops", Icon: Activity },
];

export function AdminLayout() {
  const { t } = useTranslation();
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
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {NAV_ITEMS.map(({ to, labelKey, Icon }) => (
            <li key={to}>
              <NavLink
                to={to}
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
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <main style={{ flex: 1, minWidth: 0 }}>
        <Outlet />
      </main>
    </div>
  );
}
