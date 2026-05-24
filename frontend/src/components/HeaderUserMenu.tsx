import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useSession } from "../api/auth";
import { useConfig } from "../api/config";

/** Header slot: login link when anonymous; avatar + name (+ admin link) when signed in.
 *  Renders nothing when the backend reports ``auth_enabled: false`` (SSO unconfigured). */
export function HeaderUserMenu() {
  const { t } = useTranslation();
  const { data: config, isLoading: configLoading } = useConfig();
  const { data: session, isLoading: sessionLoading } = useSession();
  if (sessionLoading || configLoading) return null;
  // Default-safe: hide login UI unless the backend explicitly confirms SSO is on.
  // Avoids a flash of "ログイン" before /api/config resolves, which would lead the
  // user to /login → SSO 未設定 dead-end.
  if (!config?.auth_enabled) return null;
  if (!session) {
    return (
      <Link to="/login" style={{ color: "inherit", textDecoration: "none", padding: "4px 12px" }}>
        {t("common.login")}
      </Link>
    );
  }
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      {session.avatar_url && (
        <img src={session.avatar_url} alt="" width={24} height={24} style={{ borderRadius: 12 }} />
      )}
      <Link to="/me" style={{ color: "inherit", textDecoration: "none" }}>
        {session.name || session.email}
      </Link>
      {session.role === "admin" && (
        <Link to="/admin/users" style={{ color: "inherit", textDecoration: "none",
                                          padding: "2px 8px", background: "var(--surface-2)",
                                          borderRadius: 4, fontSize: 12 }}>
          {t("account.admin_link")}
        </Link>
      )}
    </div>
  );
}
