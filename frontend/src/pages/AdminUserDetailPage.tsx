import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
  const { uid } = useParams<{ uid: string }>();
  const { data, isLoading, error } = useQuery({
    queryKey: ["adminUser", uid],
    queryFn: () => apiGet<Detail>(`/api/admin/users/${uid}`),
  });
  if (isLoading) return <div style={{ padding: 24 }}>{t("common.loading")}</div>;
  if (error || !data) {
    return (
      <div style={{ padding: 24 }}>
        {t("admin.user_detail.error", { msg: formatApiError(error) })}
      </div>
    );
  }
  return (
    <div style={{ padding: 24, maxWidth: 720 }}>
      <h1 style={{ fontSize: 22, marginBottom: 16 }}>{data.email}</h1>
      <div style={{ marginBottom: 24 }}>
        <div>{t("admin.user_detail.name_label")}: {data.name ?? "-"}</div>
        <div>{t("account.role_label")}: {data.role}</div>
        <div>
          {t("admin.user_detail.status_label")}:{" "}
          {data.suspended_at ? t("admin.users.status.suspended") : t("admin.users.status.active")}
        </div>
        <div>
          {t("admin.user_detail.created_label")}: {new Date(data.created_at).toLocaleString("ja-JP")}
        </div>
      </div>
      <section style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 16, marginBottom: 8 }}>{t("account.linked_providers")}</h2>
        <ul>
          {data.identities.map((i) => (
            <li key={`${i.provider}-${i.provider_sub}`}>
              {i.provider} ({t("admin.user_detail.email_at_link", { email: i.email_at_link ?? "?" })})
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h2 style={{ fontSize: 16, marginBottom: 8 }}>{t("admin.user_detail.recent_events")}</h2>
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
