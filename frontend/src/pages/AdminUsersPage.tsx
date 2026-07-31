import { Link, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAdminUsers, useDeleteUser, usePatchUser } from "../api/admin";
import { formatApiError } from "../api/client";
import { AdminAvatar, AdminButton, AdminSearchInput, StatusChip } from "./admin/adminControls";

const PAGE_SIZE = 50;

/** Admin: searchable, filterable, paginated user list with inline role / suspend / delete controls. */
export function AdminUsersPage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const q = searchParams.get("q") ?? "";
  const role = searchParams.get("role") ?? "";
  const suspended = searchParams.get("suspended") ?? "";
  const page = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);

  const { data, isLoading, error } = useAdminUsers({
    q,
    role,
    suspended,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  });
  const patch = usePatchUser();
  const del = useDeleteUser();

  function setFilter(key: "q" | "role" | "suspended", value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("page");
    setSearchParams(next);
  }

  function setPage(nextPage: number) {
    const next = new URLSearchParams(searchParams);
    next.set("page", String(nextPage));
    setSearchParams(next);
  }

  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 22, marginBottom: 16 }}>{t("admin.users.title")}</h1>
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
        <AdminSearchInput
          placeholder={t("admin.users.search_placeholder")}
          value={q}
          onChange={(e) => setFilter("q", e.target.value)}
        />
        <select
          aria-label={t("admin.users.filter.role_all")}
          value={role}
          onChange={(e) => setFilter("role", e.target.value)}
        >
          <option value="">{t("admin.users.filter.role_all")}</option>
          <option value="user">{t("admin.users.filter.role_user")}</option>
          <option value="admin">{t("admin.users.filter.role_admin")}</option>
        </select>
        <select
          aria-label={t("admin.users.filter.status_all")}
          value={suspended}
          onChange={(e) => setFilter("suspended", e.target.value)}
        >
          <option value="">{t("admin.users.filter.status_all")}</option>
          <option value="false">{t("admin.users.status.active")}</option>
          <option value="true">{t("admin.users.status.suspended")}</option>
        </select>
      </div>
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
          {data && data.users.length === 0 && (
            <tr>
              <td colSpan={5} style={{ textAlign: "center", color: "var(--text-tertiary)", padding: 24 }}>
                {t("admin.users.empty")}
              </td>
            </tr>
          )}
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
      <div style={{ marginTop: 12, display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div style={{ color: "var(--text-tertiary)", fontSize: 12 }}>
          {t("admin.users.total", { count: total })}
        </div>
        {totalPages > 1 && (
          <div style={{ display: "flex", gap: 4 }}>
            <AdminButton variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              {t("admin.users.pagination.prev")}
            </AdminButton>
            {Array.from({ length: totalPages }, (_, i) => i + 1).map((n) => (
              <AdminButton key={n} variant={n === page ? "primary" : "secondary"} onClick={() => setPage(n)}>
                {n}
              </AdminButton>
            ))}
            <AdminButton variant="secondary" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
              {t("admin.users.pagination.next")}
            </AdminButton>
          </div>
        )}
      </div>
    </div>
  );
}
