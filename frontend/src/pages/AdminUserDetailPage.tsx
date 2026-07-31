import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { apiGet, formatApiError } from "../api/client";
import { usePatchUser, useDeleteUser } from "../api/admin";
import { useSession } from "../api/auth";
import { AdminButton } from "./admin/adminControls";

type Detail = {
  user_id: number;
  email: string;
  name: string | null;
  avatar_url: string | null;
  role: "user" | "admin";
  suspended_at: string | null;
  created_at: string;
  identities: { provider: string; provider_sub: string; email_at_link: string | null; created_at: string }[];
  recent_events: {
    event_id: number;
    kind: string;
    provider: string | null;
    meta: Record<string, unknown> | null;
    created_at: string;
  }[];
};

/** Admin: detail view for a single user with identities, recent audit events, and inline role/suspend/delete actions. */
export function AdminUserDetailPage() {
  const { t, i18n } = useTranslation();
  const { uid } = useParams<{ uid: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const listSearch = (location.state as { listSearch?: string } | null)?.listSearch;
  const { data, isLoading, error } = useQuery({
    queryKey: ["adminUser", uid],
    queryFn: ({ signal }) => apiGet<Detail>(`/api/admin/users/${uid}`, { signal }),
  });
  const { data: me } = useSession();
  const patch = usePatchUser();
  const del = useDeleteUser();

  if (isLoading) return <div style={{ padding: 24 }}>{t("common.loading")}</div>;
  if (!data) {
    return (
      <div style={{ padding: 24 }}>
        {t("admin.user_detail.error", { msg: formatApiError(error) })}
      </div>
    );
  }

  const isSelf = me?.user_id === data.user_id;
  const isMutating = patch.isPending || del.isPending;

  function handleRoleChange(nextRole: string) {
    if (nextRole === "admin" && !confirm(t("admin.users.confirm_promote", { email: data!.email }))) return;
    del.reset();
    patch.mutate({ uid: data!.user_id, body: { role: nextRole } });
  }

  function handleSuspendToggle() {
    del.reset();
    patch.mutate({ uid: data!.user_id, body: { suspended: !data!.suspended_at } });
  }

  function handleDelete() {
    if (!confirm(t("admin.users.confirm_delete", { email: data!.email }))) return;
    patch.reset();
    del.mutate(data!.user_id, { onSuccess: () => navigate("/admin/users") });
  }

  return (
    <div style={{ padding: 24, maxWidth: 920 }}>
      <Link
        to={{ pathname: "/admin/users", search: listSearch ? `?${listSearch}` : "" }}
        style={{ fontSize: 13, color: "var(--text-tertiary)" }}
      >
        ← {t("admin.user_detail.back_to_users")}
      </Link>
      <h1 style={{ fontSize: 22, margin: "12px 0 16px" }}>{data.email}</h1>
      {error && (
        <div role="alert" style={{ marginBottom: 16, padding: 8, background: "var(--surface-2)",
                                    borderRadius: 4, fontSize: 13, color: "var(--text-tertiary)" }}>
          {t("admin.user_detail.error", { msg: formatApiError(error) })}
        </div>
      )}
      <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
        <div style={{ flex: 2, minWidth: 280 }}>
          <div style={{ marginBottom: 24 }}>
            <div>{t("admin.user_detail.name_label")}: {data.name ?? "-"}</div>
            <div>
              {t("admin.user_detail.status_label")}:{" "}
              {data.suspended_at ? t("admin.users.status.suspended") : t("admin.users.status.active")}
            </div>
            <div>
              {t("admin.user_detail.created_label")}: {new Date(data.created_at).toLocaleString(i18n.language)}
            </div>
          </div>
          <section style={{ marginBottom: 24 }}>
            <h2 style={{ fontSize: 16, marginBottom: 8 }}>{t("account.linked_providers")}</h2>
            <ul>
              {data.identities.map((idn) => (
                <li key={`${idn.provider}-${idn.provider_sub}`}>
                  {idn.provider} ({t("admin.user_detail.email_at_link", { email: idn.email_at_link ?? "?" })})
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
                  {new Date(e.created_at).toLocaleString(i18n.language)}
                </div>
                {e.meta && <pre style={{ margin: "4px 0", fontSize: 12 }}>{JSON.stringify(e.meta)}</pre>}
              </div>
            ))}
          </section>
        </div>
        <div style={{ flex: 1, minWidth: 220, borderLeft: "1px solid var(--surface-2)", paddingLeft: 24 }}>
          <div style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: "0.03em",
                        color: "var(--text-secondary)", marginBottom: 10 }}>
            {t("admin.user_detail.actions_title")}
          </div>
          {isSelf && (
            <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 10 }}>
              {t("admin.user_detail.self_hint")}
            </div>
          )}
          <label htmlFor="detail-role" style={{ display: "block", marginBottom: 10 }}>
            <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 4 }}>
              {t("account.role_label")}
            </div>
            <select
              id="detail-role"
              value={data.role}
              style={{ width: "100%" }}
              disabled={isSelf || isMutating}
              onChange={(e) => handleRoleChange(e.target.value)}
            >
              <option value="user">{t("account.role.user")}</option>
              <option value="admin">{t("account.role.admin")}</option>
            </select>
          </label>
          <AdminButton
            variant="secondary"
            style={{ width: "100%", marginBottom: 8, justifyContent: "center" }}
            disabled={isSelf || isMutating}
            onClick={handleSuspendToggle}
          >
            {data.suspended_at ? t("admin.users.action.resume") : t("admin.users.action.suspend")}
          </AdminButton>
          <AdminButton
            variant="danger"
            style={{ width: "100%", justifyContent: "center" }}
            disabled={isSelf || isMutating}
            onClick={handleDelete}
          >
            {t("admin.users.action.delete")}
          </AdminButton>
          {(patch.error || del.error) && (
            <div role="alert" style={{ marginTop: 10, padding: 8, background: "var(--surface-2)",
                                        borderRadius: 4, fontSize: 13, color: "var(--text-tertiary)" }}>
              {formatApiError(patch.error || del.error)}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
