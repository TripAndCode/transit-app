import { Link } from "react-router-dom";
import { useSession } from "../api/auth";

/** Header slot: login link when anonymous; avatar + name (+ admin link) when signed in. */
export function HeaderUserMenu() {
  const { data: session, isLoading } = useSession();
  if (isLoading) return null;
  if (!session) {
    return (
      <Link to="/login" style={{ color: "inherit", textDecoration: "none", padding: "4px 12px" }}>
        ログイン
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
          管理
        </Link>
      )}
    </div>
  );
}
