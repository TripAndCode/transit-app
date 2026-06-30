import { NavLink, Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";

const NAV_ITEMS = [
  { to: "/admin/agencies", labelKey: "admin.nav.agencies" },
  { to: "/admin/users", labelKey: "admin.nav.users" },
  { to: "/admin/ops", labelKey: "admin.nav.ops" },
] as const;

export function AdminLayout() {
  const { t } = useTranslation();
  return (
    <div style={{ display: "flex", minHeight: "100%", flex: 1 }}>
      <nav
        aria-label={t("admin.nav.label")}
        style={{
          width: 180,
          borderRight: "1px solid var(--border-soft)",
          padding: "20px 0",
          flexShrink: 0,
        }}
      >
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {NAV_ITEMS.map(({ to, labelKey }) => (
            <li key={to}>
              <NavLink
                to={to}
                style={({ isActive }) => ({
                  display: "block",
                  padding: "8px 20px",
                  textDecoration: "none",
                  color: isActive ? "var(--accent)" : "var(--text-primary)",
                  fontWeight: isActive ? 600 : 400,
                  fontSize: 14,
                  background: isActive ? "var(--accent-soft)" : "transparent",
                })}
              >
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
