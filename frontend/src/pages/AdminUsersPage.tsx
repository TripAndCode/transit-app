import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAdminUsers, useDeleteUser, usePatchUser } from "../api/admin";
import { formatApiError } from "../api/client";
import { AdminAvatar, AdminButton, AdminSearchInput, StatusChip } from "./admin/adminControls";

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
      <AdminSearchInput
        placeholder={t("admin.users.search_placeholder")}
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {error && <div style={{ color: "var(--text-tertiary)" }}>{formatApiError(error)}</div>}
      {isLoading && <div>{t("common.loading")}</div>}
      <table className="admin-table">
        <thead>
          <tr>
            <th>{t("admin.users.col.email")}</th>
            <th>{t("admin.users.col.name")}</th>
            <th>{t("admin.users.col.role")}</th>
            <th>{t("admin.users.col.status")}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {data?.users.map((u) => (
            <tr key={u.user_id}>
              <td>
                <AdminAvatar label={u.name || u.email} />
                <Link to={`/admin/users/${u.user_id}`}>{u.email}</Link>
              </td>
              <td>{u.name ?? "-"}</td>
              <td>
                <select
                  value={u.role}
                  onChange={(e) => patch.mutate({ uid: u.user_id, body: { role: e.target.value } })}
                >
                  <option value="user">user</option>
                  <option value="admin">admin</option>
                </select>
              </td>
              <td>
                <StatusChip tone={u.suspended_at ? "warn" : "good"}>
                  {u.suspended_at ? t("admin.users.status.suspended") : t("admin.users.status.active")}
                </StatusChip>
              </td>
              <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                <AdminButton
                  variant="secondary"
                  onClick={() => patch.mutate({ uid: u.user_id, body: { suspended: !u.suspended_at } })}
                  style={{ marginRight: 8 }}
                >
                  {u.suspended_at ? t("admin.users.action.resume") : t("admin.users.action.suspend")}
                </AdminButton>
                <AdminButton
                  variant="danger"
                  onClick={() => {
                    if (confirm(t("admin.users.confirm_delete", { email: u.email }))) {
                      del.mutate(u.user_id);
                    }
                  }}
                >
                  {t("admin.users.action.delete")}
                </AdminButton>
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
