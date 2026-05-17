import { useQuery } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";
import { useLogout, useSession } from "../api/auth";

type SessionRow = {
  sid_prefix: string;
  user_agent: string | null;
  ip: string | null;
  created_at: string;
  last_seen_at: string;
};

async function fetchMySessions(): Promise<SessionRow[]> {
  const r = await fetch("/api/me/sessions", { credentials: "include" });
  if (!r.ok) throw new Error(`/api/me/sessions ${r.status}`);
  return (await r.json()) as SessionRow[];
}

/** Self-service profile + active sessions + logout. */
export function AccountPage() {
  const { data: session, isLoading } = useSession();
  const { data: sessions } = useQuery({ queryKey: ["mySessions"], queryFn: fetchMySessions });
  const logout = useLogout();

  if (isLoading) return <div style={{ padding: 24 }}>読み込み中...</div>;
  if (!session) return <Navigate to="/login" replace />;

  return (
    <div style={{ maxWidth: 640, margin: "32px auto", padding: 24 }}>
      <h1 style={{ fontSize: 22, marginBottom: 16 }}>アカウント</h1>
      <section style={{ marginBottom: 24 }}>
        <div>{session.email}</div>
        <div style={{ color: "var(--text-tertiary)" }}>{session.name ?? ""}</div>
        <div style={{ color: "var(--text-tertiary)", fontSize: 13, marginTop: 4 }}>
          ロール: {session.role === "admin" ? "管理者" : "ユーザー"}
        </div>
      </section>
      <section style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 16, marginBottom: 8 }}>連携プロバイダ</h2>
        <ul>{session.identities.map((i) => <li key={i.provider}>{i.provider}</li>)}</ul>
      </section>
      <section style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 16, marginBottom: 8 }}>アクティブなセッション</h2>
        {sessions?.map((s) => (
          <div key={s.sid_prefix} style={{ padding: 8, background: "var(--surface-1)",
                                            borderRadius: 4, marginBottom: 4, fontSize: 13 }}>
            <div>{s.user_agent ?? "(unknown UA)"}</div>
            <div style={{ color: "var(--text-tertiary)" }}>
              最終: {new Date(s.last_seen_at).toLocaleString("ja-JP")}
            </div>
          </div>
        ))}
      </section>
      <button
        onClick={() => logout.mutate(undefined, { onSuccess: () => (window.location.href = "/") })}
        disabled={logout.isPending}
        style={{ padding: "8px 16px", background: "var(--surface-2)", border: "none", borderRadius: 4 }}
      >
        ログアウト
      </button>
    </div>
  );
}
