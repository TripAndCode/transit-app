import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAdminUsers, useDeleteUser, usePatchUser } from "../api/admin";
import { formatApiError } from "../api/client";

/** Status pill matching the color-coded chip pattern already established by
 *  AdminAgenciesPage's active/deleted badge and AdminOpsPage's FreshnessChip —
 *  this page previously showed suspended users with an uncolored pill and
 *  active users with a bare "—", the only admin table not using the pattern. */
function StatusChip({ suspended, t }: { suspended: boolean; t: ReturnType<typeof useTranslation>["t"] }) {
  if (suspended) {
    return (
      <span style={{ fontSize: 12, padding: "2px 8px", borderRadius: 4, background: "var(--surface-2)", color: "var(--color-warning, #C99A2E)" }}>
        {t("admin.users.status.suspended")}
      </span>
    );
  }
  return (
    <span style={{ fontSize: 12, padding: "2px 8px", borderRadius: 4, background: "var(--accent-soft)", color: "var(--accent)" }}>
      {t("admin.users.status.active")}
    </span>
  );
}

/** Admin: searchable user list with inline role / suspend / delete controls. */
export function AdminUsersPage() {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const { data, isLoading, error } = useAdminUsers({ q });
  const patch = usePatchUser();
  const del = useDeleteUser();

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 22, marginBottom: 16 }}>{t("admin.users.title")}</h1>
      <input
        type="search"
        placeholder={t("admin.users.search_placeholder")}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        style={{ padding: 8, marginBottom: 16, width: 320 }}
      />
      {error && <div style={{ color: "var(--text-tertiary)" }}>{formatApiError(error)}</div>}
      {isLoading && <div>{t("common.loading")}</div>}
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
        <thead>
          <tr style={{ background: "var(--surface-1)" }}>
            <th style={{ padding: "8px 12px", textAlign: "left" }}>{t("admin.users.col.email")}</th>
            <th style={{ padding: "8px 12px", textAlign: "left" }}>{t("admin.users.col.name")}</th>
            <th style={{ padding: "8px 12px", textAlign: "left" }}>{t("admin.users.col.role")}</th>
            <th style={{ padding: "8px 12px", textAlign: "left" }}>{t("admin.users.col.status")}</th>
            <th style={{ padding: "8px 12px" }}></th>
          </tr>
        </thead>
        <tbody>
          {data?.users.map((u) => (
            <tr key={u.user_id} style={{ borderBottom: "1px solid var(--surface-2)" }}>
              <td style={{ padding: "8px 12px" }}>
                <Link to={`/admin/users/${u.user_id}`} style={{ color: "inherit" }}>{u.email}</Link>
              </td>
              <td style={{ padding: "8px 12px" }}>{u.name ?? "-"}</td>
              <td style={{ padding: "8px 12px" }}>
                <select
                  value={u.role}
                  onChange={(e) => patch.mutate({ uid: u.user_id, body: { role: e.target.value } })}
                >
                  <option value="user">user</option>
                  <option value="admin">admin</option>
                </select>
              </td>
              <td style={{ padding: "8px 12px" }}>
                <StatusChip suspended={!!u.suspended_at} t={t} />
              </td>
              <td style={{ padding: "8px 12px", textAlign: "right" }}>
                <button
                  onClick={() => patch.mutate({ uid: u.user_id, body: { suspended: !u.suspended_at } })}
                  style={{ marginRight: 8 }}
                >
                  {u.suspended_at ? t("admin.users.action.resume") : t("admin.users.action.suspend")}
                </button>
                <button
                  onClick={() => {
                    if (confirm(t("admin.users.confirm_delete", { email: u.email }))) {
                      del.mutate(u.user_id);
                    }
                  }}
                >
                  {t("admin.users.action.delete")}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {(patch.error || del.error) && (
        <div role="alert" style={{ marginTop: 8, padding: 8, background: "var(--surface-2)",
                                    borderRadius: 4, fontSize: 13, color: "var(--text-tertiary)" }}>
          {formatApiError(patch.error || del.error)}
        </div>
      )}
      <div style={{ marginTop: 12, color: "var(--text-tertiary)", fontSize: 12 }}>
        {t("admin.users.total", { count: data?.total ?? 0 })}
      </div>
    </div>
  );
}
