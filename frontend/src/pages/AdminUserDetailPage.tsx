import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { apiGet, formatApiError } from "../api/client";

type Detail = {
  user_id: number;
  email: string;
  name: string | null;
  avatar_url: string | null;
  role: "user" | "admin";
  suspended_at: string | null;
  created_at: string;
  identities: { provider: string; provider_sub: string; email_at_link: string | null; created_at: string }[];
  recent_events: { event_id: number; kind: string; provider: string | null; meta: any; created_at: string }[];
};

/** Admin: detail view for a single user with identities and recent audit events. */
export function AdminUserDetailPage() {
  const { uid } = useParams<{ uid: string }>();
  const { data, isLoading, error } = useQuery({
    queryKey: ["adminUser", uid],
    queryFn: () => apiGet<Detail>(`/api/admin/users/${uid}`),
  });
  if (isLoading) return <div style={{ padding: 24 }}>読み込み中...</div>;
  if (error || !data) return <div style={{ padding: 24 }}>エラー: {formatApiError(error)}</div>;
  return (
    <div style={{ padding: 24, maxWidth: 720 }}>
      <h1 style={{ fontSize: 22, marginBottom: 16 }}>{data.email}</h1>
      <div style={{ marginBottom: 24 }}>
        <div>名前: {data.name ?? "-"}</div>
        <div>ロール: {data.role}</div>
        <div>状態: {data.suspended_at ? "停止中" : "アクティブ"}</div>
        <div>作成: {new Date(data.created_at).toLocaleString("ja-JP")}</div>
      </div>
      <section style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 16, marginBottom: 8 }}>連携プロバイダ</h2>
        <ul>
          {data.identities.map((i) => (
            <li key={`${i.provider}-${i.provider_sub}`}>
              {i.provider} (連携時メール: {i.email_at_link ?? "?"})
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h2 style={{ fontSize: 16, marginBottom: 8 }}>最近のイベント</h2>
        {data.recent_events.map((e) => (
          <div key={e.event_id} style={{ padding: 8, background: "var(--surface-1)",
                                          borderRadius: 4, marginBottom: 4, fontSize: 13 }}>
            <div>{e.kind} {e.provider ? `(${e.provider})` : ""}</div>
            <div style={{ color: "var(--text-tertiary)" }}>
              {new Date(e.created_at).toLocaleString("ja-JP")}
            </div>
            {e.meta && <pre style={{ margin: "4px 0", fontSize: 12 }}>{JSON.stringify(e.meta)}</pre>}
          </div>
        ))}
      </section>
    </div>
  );
}
