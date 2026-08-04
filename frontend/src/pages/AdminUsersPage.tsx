import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAdminUsers, useDeleteUser, usePatchUser } from "../api/admin";
import { useSession } from "../api/auth";
import { formatApiError } from "../api/client";
import { AdminAvatar, AdminButton, AdminSearchInput, StatusChip } from "./admin/adminControls";
import { pageItems } from "./admin/pageItems";

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;

/** Admin: searchable, filterable, paginated user list with inline role / suspend / delete controls. */
export function AdminUsersPage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const q = searchParams.get("q") ?? "";
  const rawRole = searchParams.get("role") ?? "";
  const role = rawRole === "user" || rawRole === "admin" ? rawRole : "";
  const rawSuspended = searchParams.get("suspended") ?? "";
  const suspended = rawSuspended === "true" || rawSuspended === "false" ? rawSuspended : "";
  const pageParam = Number(searchParams.get("page") ?? "1");
  const rawPage = Number.isFinite(pageParam) ? Math.max(1, Math.floor(pageParam)) : 1;

  const { data: me } = useSession();

  // The search box is debounced locally so the URL/query key (and therefore
  // the backend ILIKE scan) doesn't change on every keystroke. Skipping when
  // the trimmed input already matches the committed `q` is what keeps this
  // effect from re-arming (and clobbering `page`) on every unrelated URL
  // change — `setSearchParams`'s identity changes on any searchParams update.
  const [qInput, setQInput] = useState(q);
  useEffect(() => {
    const trimmed = qInput.trim();
    if (trimmed === q) return;
    const id = setTimeout(() => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        if (trimmed) next.set("q", trimmed);
        else next.delete("q");
        next.delete("page");
        return next;
      }, { replace: true });
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [qInput, q, setSearchParams]);

  const { data, isLoading, isPlaceholderData, error } = useAdminUsers({
    q,
    role,
    suspended,
    limit: PAGE_SIZE,
    offset: (rawPage - 1) * PAGE_SIZE,
  });
  const patch = usePatchUser();
  const del = useDeleteUser();

  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const page = Math.min(rawPage, totalPages);

  // A stale/shared ?page= beyond the current result set (filters changed,
  // rows disappeared) self-heals to the last real page instead of stranding
  // the admin on a blank table with no visible way back.
  useEffect(() => {
    if (data && rawPage > totalPages) {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.set("page", String(totalPages));
        return next;
      }, { replace: true });
    }
  }, [data, rawPage, totalPages, setSearchParams]);

  function setFilter(key: "role" | "suspended", value: string) {
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

  function isRowLocked(uid: number) {
    return (
      isPlaceholderData ||
      uid === me?.user_id ||
      (patch.isPending && patch.variables?.uid === uid) ||
      (del.isPending && del.variables === uid)
    );
  }

  function handleRoleChange(uid: number, email: string, nextRole: string) {
    if (nextRole === "admin" && !confirm(t("admin.users.confirm_promote", { email }))) return;
    del.reset();
    patch.mutate({ uid, body: { role: nextRole } });
  }

  function handleSuspendToggle(uid: number, suspendedAt: string | null) {
    del.reset();
    patch.mutate({ uid, body: { suspended: !suspendedAt } });
  }

  function handleDelete(uid: number, email: string) {
    if (!confirm(t("admin.users.confirm_delete", { email }))) return;
    patch.reset();
    del.mutate(uid);
  }

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 22, marginBottom: 16 }}>{t("admin.users.title")}</h1>
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
        <AdminSearchInput
          placeholder={t("admin.users.search_placeholder")}
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
        />
        <select
          aria-label={t("account.role_label")}
          value={role}
          onChange={(e) => setFilter("role", e.target.value)}
        >
          <option value="">{t("admin.users.filter.role_all")}</option>
          <option value="user">{t("account.role.user")}</option>
          <option value="admin">{t("account.role.admin")}</option>
        </select>
        <select
          aria-label={t("admin.users.col.status")}
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
      <table className="admin-table" style={{ opacity: isPlaceholderData ? 0.6 : 1 }}>
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
                <Link to={`/admin/users/${u.user_id}`} state={{ listSearch: searchParams.toString() }}>
                  {u.email}
                </Link>
              </td>
              <td>{u.name ?? "-"}</td>
              <td>
                <select
                  value={u.role}
                  disabled={isRowLocked(u.user_id)}
                  onChange={(e) => handleRoleChange(u.user_id, u.email, e.target.value)}
                >
                  <option value="user">{t("account.role.user")}</option>
                  <option value="admin">{t("account.role.admin")}</option>
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
                  disabled={isRowLocked(u.user_id)}
                  onClick={() => handleSuspendToggle(u.user_id, u.suspended_at)}
                  style={{ marginRight: 8 }}
                >
                  {u.suspended_at ? t("admin.users.action.resume") : t("admin.users.action.suspend")}
                </AdminButton>
                <AdminButton
                  variant="danger"
                  disabled={isRowLocked(u.user_id)}
                  onClick={() => handleDelete(u.user_id, u.email)}
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
        {(totalPages > 1 || rawPage > 1) && (
          <div style={{ display: "flex", gap: 4 }}>
            <AdminButton variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              {t("admin.users.pagination.prev")}
            </AdminButton>
            {pageItems(page, totalPages).map((item, i) =>
              item === "ellipsis" ? (
                <span key={`ellipsis-${i}`} aria-hidden="true" style={{ padding: "0 4px", color: "var(--text-tertiary)" }}>
                  …
                </span>
              ) : (
                <AdminButton key={item} variant={item === page ? "primary" : "secondary"} onClick={() => setPage(item)}>
                  {item}
                </AdminButton>
              )
            )}
            <AdminButton variant="secondary" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
              {t("admin.users.pagination.next")}
            </AdminButton>
          </div>
        )}
      </div>
    </div>
  );
}
