import { useQuery } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useLogout, useSession } from "../api/auth";
import { apiGet } from "../api/client";

type SessionRow = {
  sid_prefix: string;
  user_agent: string | null;
  ip: string | null;
  created_at: string;
  last_seen_at: string;
};

/** Self-service profile + active sessions + logout. */
export function AccountPage() {
  const { t } = useTranslation();
  const { data: session, isLoading } = useSession();
  const { data: sessions } = useQuery({
    queryKey: ["mySessions"],
    queryFn: () => apiGet<SessionRow[]>("/api/me/sessions"),
  });
  const logout = useLogout();

  if (isLoading) return <div style={{ padding: 24 }}>{t("common.loading")}</div>;
  if (!session) return <Navigate to="/login" replace />;

  return (
    <div style={{ maxWidth: 640, margin: "32px auto", padding: 24 }}>
      <h1 style={{ fontSize: 22, marginBottom: 16 }}>{t("account.title")}</h1>
      <section style={{ marginBottom: 24 }}>
        <div>{session.email}</div>
        <div style={{ color: "var(--text-tertiary)" }}>{session.name ?? ""}</div>
        <div style={{ color: "var(--text-tertiary)", fontSize: 13, marginTop: 4 }}>
          {t("account.role_label")}: {session.role === "admin" ? t("account.role.admin") : t("account.role.user")}
        </div>
      </section>
      <section style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 16, marginBottom: 8 }}>{t("account.linked_providers")}</h2>
        <ul>{session.identities.map((i) => <li key={i.provider}>{i.provider}</li>)}</ul>
      </section>
      <section style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 16, marginBottom: 8 }}>{t("account.active_sessions")}</h2>
        {sessions?.map((s) => (
          <div key={s.sid_prefix} style={{ padding: 8, background: "var(--surface-1)",
                                            borderRadius: 4, marginBottom: 4, fontSize: 13 }}>
            <div>{s.user_agent ?? "(unknown UA)"}</div>
            <div style={{ color: "var(--text-tertiary)" }}>
              {t("account.session_last_seen", { when: new Date(s.last_seen_at).toLocaleString("ja-JP") })}
            </div>
          </div>
        ))}
      </section>
      <button
        onClick={() => logout.mutate(undefined, { onSuccess: () => (window.location.href = "/") })}
        disabled={logout.isPending}
        style={{ padding: "8px 16px", background: "var(--surface-2)", color: "var(--text-primary)", border: "none", borderRadius: 4 }}
      >
        {t("account.logout")}
      </button>
    </div>
  );
}
