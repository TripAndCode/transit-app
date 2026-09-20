import { useEffect, useEffectEvent, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams, type SetURLSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAdminUsers, useBulkPatchUsers, useDeleteUser, usePatchUser, type UserPatchBody } from "../../api/admin";
import { useSession } from "../../api/auth";
import { ErrorBanner } from "../../components/ErrorBanner";
import { PageHeader } from "../../components/ui/PageHeader";
import { AdminAvatar, AdminButton, AdminSearchInput, StatusChip } from "./adminControls";
import { pageItems } from "./pageItems";

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;
const UNDO_WINDOW_MS = 8000;

type SavedView = "all" | "pending" | "admin" | "suspended";
const SAVED_VIEWS: SavedView[] = ["all", "pending", "admin", "suspended"];

type BulkAction = "approve" | "suspend" | "promote" | "demote";

const BULK_PATCH: Record<BulkAction, UserPatchBody> = {
  approve: { llm_approved: true },
  suspend: { suspended: true },
  promote: { role: "admin" },
  demote: { role: "user" },
};
const BULK_INVERSE: Record<BulkAction, UserPatchBody> = {
  approve: { llm_approved: false },
  suspend: { suspended: false },
  promote: { role: "user" },
  demote: { role: "admin" },
};
const BULK_TOAST_KEY: Record<BulkAction, string> = {
  approve: "approved",
  suspend: "suspended",
  promote: "promoted",
  demote: "demoted",
};

/** True for an element that consumes plain-letter keystrokes as text input,
 * so the j/k/x/a// shortcuts below don't fire while the admin is typing. */
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA" || target.isContentEditable;
}

/** The search box's own local-edit + debounce-commit state, extracted so the
 *  displayed value can track `q` for any reason it changes -- including a
 *  browser back/forward, not just this component's own debounce commit --
 *  without losing focus. `qOverride` is the in-progress local edit (`null`
 *  when there isn't one); the displayed value is `qOverride ?? q`. A `q`
 *  change from any source discards a stale override during render (React's
 *  documented "adjusting state when a prop changes" pattern comparing
 *  against `committedQ`, not an effect, so this never causes a remount that
 *  would drop focus the way keying this component on `q` would). Without
 *  this reset, `qOverride` would keep echoing back whatever was last typed
 *  even after external navigation changed `q` to something else. */
function AdminUserSearchBox({
  q,
  setSearchParams,
  placeholder,
  inputRef,
}: {
  q: string;
  setSearchParams: SetURLSearchParams;
  placeholder: string;
  inputRef?: React.Ref<HTMLInputElement>;
}) {
  const [committedQ, setCommittedQ] = useState(q);
  const [qOverride, setQOverride] = useState<string | null>(null);
  if (q !== committedQ) {
    setCommittedQ(q);
    setQOverride(null);
  }
  const qInput = qOverride ?? q;

  useEffect(() => {
    if (qOverride == null) return;
    const trimmed = qOverride.trim();
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
  }, [qOverride, q, setSearchParams]);

  return (
    <AdminSearchInput
      ref={inputRef}
      placeholder={placeholder}
      value={qInput}
      onChange={(e) => setQOverride(e.target.value)}
    />
  );
}

/** Admin: searchable, filterable, paginated user list with bulk selection (checkbox
 *  column, floating bulk-action bar, 8s undo), saved views, keyboard navigation
 *  (j/k move focus, x toggles selection, a approves, / focuses search, Enter opens),
 *  and inline role / suspend / delete controls. */
export function AdminUsersPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const q = searchParams.get("q") ?? "";
  const rawRole = searchParams.get("role") ?? "";
  const role = rawRole === "user" || rawRole === "admin" ? rawRole : "";
  const rawSuspended = searchParams.get("suspended") ?? "";
  const suspended = rawSuspended === "true" || rawSuspended === "false" ? rawSuspended : "";
  const rawLlmApproved = searchParams.get("llm_approved") ?? "";
  const llmApproved = rawLlmApproved === "true" || rawLlmApproved === "false" ? rawLlmApproved : "";
  const pageParam = Number(searchParams.get("page") ?? "1");
  const rawPage = Number.isFinite(pageParam) ? Math.max(1, Math.floor(pageParam)) : 1;

  const { data: me } = useSession();

  // Badge count for the "awaiting approval" saved view — a separate,
  // cheap (limit=1) query so the count stays accurate regardless of the
  // currently-active filters/page.
  const { data: pendingData } = useAdminUsers({ llmApproved: "false", limit: 1, offset: 0 });
  const pendingCount = pendingData?.total ?? 0;

  const { data, isLoading, isPlaceholderData, error, refetch } = useAdminUsers({
    q,
    role,
    suspended,
    llmApproved,
    limit: PAGE_SIZE,
    offset: (rawPage - 1) * PAGE_SIZE,
  });
  const patch = usePatchUser();
  const del = useDeleteUser();
  const bulkPatch = useBulkPatchUsers();

  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const page = Math.min(rawPage, totalPages);

  // A stale/shared ?page= beyond the current result set (filters changed,
  // rows disappeared) self-heals to the last real page instead of stranding
  // the admin on a blank table with no visible way back.
  useEffect(() => {
    if (data && rawPage > totalPages) {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("page", String(totalPages));
          return next;
        },
        { replace: true },
      );
    }
  }, [data, rawPage, totalPages, setSearchParams]);

  const rows = data?.users ?? [];

  // Selection and keyboard focus reset whenever the visible row set's
  // identity changes (filters or page) — adjusted during render rather than
  // via a synchronization effect (react-hooks/set-state-in-effect is an
  // error in this repo; this is React's documented "adjusting state during
  // render" alternative).
  const rowSetKey = `${q}|${role}|${suspended}|${llmApproved}|${page}`;
  const [priorRowSetKey, setPriorRowSetKey] = useState(rowSetKey);
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [focusedIndex, setFocusedIndex] = useState(0);
  if (rowSetKey !== priorRowSetKey) {
    setPriorRowSetKey(rowSetKey);
    setSelected(new Set());
    setFocusedIndex(0);
  }

  const [undo, setUndo] = useState<{ message: string; ids: number[]; inverse: UserPatchBody } | null>(null);
  const undoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    return () => {
      if (undoTimerRef.current) clearTimeout(undoTimerRef.current);
    };
  }, []);

  const searchInputRef = useRef<HTMLInputElement>(null);

  function isSelectable(uid: number) {
    // The signed-in admin's own row can't be bulk-mutated (mirrors the
    // per-row disable below), so it's excluded from selection entirely
    // rather than relying only on the endpoint's self-guard.
    return uid !== me?.user_id;
  }

  function isRowLocked(uid: number) {
    return (
      isPlaceholderData ||
      uid === me?.user_id ||
      (patch.isPending && patch.variables?.uid === uid) ||
      (del.isPending && del.variables === uid)
    );
  }

  function toggleSelected(uid: number) {
    if (!isSelectable(uid)) return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(uid)) next.delete(uid);
      else next.add(uid);
      return next;
    });
  }

  const selectableRows = rows.filter((u) => isSelectable(u.user_id));
  const allVisibleSelected = selectableRows.length > 0 && selectableRows.every((u) => selected.has(u.user_id));

  function toggleSelectAll() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) {
        for (const u of selectableRows) next.delete(u.user_id);
      } else {
        for (const u of selectableRows) next.add(u.user_id);
      }
      return next;
    });
  }

  function showUndo(message: string, ids: number[], inverse: UserPatchBody) {
    if (undoTimerRef.current) clearTimeout(undoTimerRef.current);
    setUndo({ message, ids, inverse });
    undoTimerRef.current = setTimeout(() => setUndo(null), UNDO_WINDOW_MS);
  }

  function runBulkAction(action: BulkAction, ids: number[]) {
    const targets = ids.filter(isSelectable);
    if (targets.length === 0) return;
    bulkPatch.mutate(
      { ids: targets, patch: BULK_PATCH[action] },
      {
        onSuccess: () => {
          setSelected(new Set());
          showUndo(
            t(`admin.users.bulk.toast.${BULK_TOAST_KEY[action]}`, { count: targets.length }),
            targets,
            BULK_INVERSE[action],
          );
        },
      },
    );
  }

  function handleUndo() {
    if (!undo) return;
    if (undoTimerRef.current) clearTimeout(undoTimerRef.current);
    bulkPatch.mutate({ ids: undo.ids, patch: undo.inverse });
    setUndo(null);
  }

  async function handleBulkDelete(ids: number[]) {
    for (const uid of ids.filter(isSelectable)) {
      const row = rows.find((u) => u.user_id === uid);
      if (!row) continue;
      if (!confirm(t("admin.users.confirm_delete", { email: row.email }))) continue;
      patch.reset();
      await del.mutateAsync(uid);
    }
    setSelected(new Set());
  }

  function setFilter(key: "role" | "suspended", value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("page");
    setSearchParams(next);
  }

  const VIEW_PARAMS: Record<SavedView, { role: string; suspended: string; llmApproved: string }> = {
    all: { role: "", suspended: "", llmApproved: "" },
    pending: { role: "", suspended: "", llmApproved: "false" },
    admin: { role: "admin", suspended: "", llmApproved: "" },
    suspended: { role: "", suspended: "true", llmApproved: "" },
  };
  const activeView: SavedView =
    suspended === "true" ? "suspended" : role === "admin" ? "admin" : llmApproved === "false" ? "pending" : "all";

  function selectView(view: SavedView) {
    const target = VIEW_PARAMS[view];
    const next = new URLSearchParams(searchParams);
    if (target.role) next.set("role", target.role);
    else next.delete("role");
    if (target.suspended) next.set("suspended", target.suspended);
    else next.delete("suspended");
    if (target.llmApproved) next.set("llm_approved", target.llmApproved);
    else next.delete("llm_approved");
    next.delete("page");
    setSearchParams(next);
  }

  function setPage(nextPage: number) {
    const next = new URLSearchParams(searchParams);
    next.set("page", String(nextPage));
    setSearchParams(next);
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

  function handleLlmApprovedToggle(uid: number, currentlyApproved: boolean) {
    del.reset();
    patch.mutate({ uid, body: { llm_approved: !currentlyApproved } });
  }

  function handleDelete(uid: number, email: string) {
    if (!confirm(t("admin.users.confirm_delete", { email }))) return;
    patch.reset();
    del.mutate(uid);
  }

  const onKeyDown = useEffectEvent((e: KeyboardEvent) => {
    if (isTypingTarget(e.target)) return;
    if (e.key === "j") {
      e.preventDefault();
      setFocusedIndex((i) => Math.min(rows.length - 1, i + 1));
    } else if (e.key === "k") {
      e.preventDefault();
      setFocusedIndex((i) => Math.max(0, i - 1));
    } else if (e.key === "x") {
      const row = rows[focusedIndex];
      if (row) {
        e.preventDefault();
        toggleSelected(row.user_id);
      }
    } else if (e.key === "a") {
      const ids = selected.size > 0 ? [...selected] : rows[focusedIndex] ? [rows[focusedIndex].user_id] : [];
      if (ids.length > 0) {
        e.preventDefault();
        runBulkAction("approve", ids);
      }
    } else if (e.key === "/") {
      e.preventDefault();
      searchInputRef.current?.focus();
    } else if (e.key === "Enter") {
      const row = rows[focusedIndex];
      if (row) navigate(`/admin/users/${row.user_id}`, { state: { listSearch: searchParams.toString() } });
    }
  });

  useEffect(() => {
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <div style={{ padding: 24 }}>
      <PageHeader title={t("admin.users.title")} />
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
        <AdminUserSearchBox
          q={q}
          setSearchParams={setSearchParams}
          placeholder={t("admin.users.search_placeholder")}
          inputRef={searchInputRef}
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
      <div role="tablist" aria-label={t("admin.users.saved_views")} style={{ display: "flex", gap: 6, flexWrap: "wrap", margin: "12px 0" }}>
        {SAVED_VIEWS.map((view) => (
          <button
            key={view}
            type="button"
            role="tab"
            aria-selected={activeView === view}
            onClick={() => selectView(view)}
            style={{
              fontSize: 12,
              padding: "4px 10px",
              borderRadius: 999,
              cursor: "pointer",
              border: `1px solid ${activeView === view ? "var(--accent)" : "var(--border-subtle)"}`,
              color: activeView === view ? "var(--accent)" : "var(--text-secondary)",
              background: activeView === view ? "var(--accent-soft)" : "transparent",
            }}
          >
            {t(`admin.users.view.${view}`)}
            {view === "pending" && pendingCount > 0 && <span style={{ marginLeft: 6 }}>{pendingCount}</span>}
          </button>
        ))}
      </div>
      {error && <ErrorBanner error={error} onRetry={() => refetch()} />}
      {isLoading && <div>{t("common.loading")}</div>}
      <table className="admin-table" style={{ opacity: isPlaceholderData ? 0.6 : 1 }}>
        <thead>
          <tr>
            <th style={{ width: 32 }}>
              <input
                type="checkbox"
                aria-label={t("admin.users.select_all")}
                checked={allVisibleSelected}
                disabled={selectableRows.length === 0}
                onChange={toggleSelectAll}
              />
            </th>
            <th>{t("admin.users.col.email")}</th>
            <th>{t("admin.users.col.name")}</th>
            <th>{t("admin.users.col.role")}</th>
            <th>{t("admin.users.col.status")}</th>
            <th>{t("admin.users.col.llm_approved")}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {data && data.users.length === 0 && (
            <tr>
              <td colSpan={7} style={{ textAlign: "center", color: "var(--text-tertiary)", padding: 24 }}>
                {t("admin.users.empty")}
              </td>
            </tr>
          )}
          {data?.users.map((u, i) => (
            <tr
              key={u.user_id}
              onClick={() => setFocusedIndex(i)}
              style={{ background: selected.has(u.user_id) ? "var(--accent-soft)" : undefined }}
            >
              <td style={{ boxShadow: focusedIndex === i ? "inset 3px 0 0 var(--accent)" : undefined }}>
                <input
                  type="checkbox"
                  aria-label={t("admin.users.select_row", { email: u.email })}
                  checked={selected.has(u.user_id)}
                  disabled={!isSelectable(u.user_id)}
                  onChange={() => toggleSelected(u.user_id)}
                />
              </td>
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
              <td>
                <StatusChip tone={u.llm_approved ? "good" : "warn"}>
                  {u.llm_approved ? t("admin.users.llm_approved.yes") : t("admin.users.llm_approved.no")}
                </StatusChip>
              </td>
              <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                <AdminButton
                  variant="secondary"
                  disabled={isRowLocked(u.user_id)}
                  onClick={() => handleLlmApprovedToggle(u.user_id, u.llm_approved)}
                  style={{ marginRight: 8 }}
                >
                  {u.llm_approved
                    ? t("admin.users.action.revoke_llm")
                    : t("admin.users.action.approve_llm")}
                </AdminButton>
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
      {(patch.error || del.error || bulkPatch.error) && (
        <ErrorBanner error={patch.error || del.error || bulkPatch.error} />
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
      {selected.size > 0 && (
        <div
          data-testid="admin-users-bulk-bar"
          style={{
            position: "fixed",
            left: "50%",
            bottom: 24,
            transform: "translateX(-50%)",
            background: "var(--surface-1)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 999,
            boxShadow: "0 8px 24px rgba(0,0,0,0.12)",
            padding: "8px 8px 8px 16px",
            display: "flex",
            gap: 8,
            alignItems: "center",
            fontSize: 13,
            zIndex: 40,
          }}
        >
          <b>{t("admin.users.bulk.selected_count", { count: selected.size })}</b>
          <AdminButton variant="secondary" onClick={() => runBulkAction("approve", [...selected])}>
            {t("admin.users.action.approve_llm")}
          </AdminButton>
          <AdminButton variant="secondary" onClick={() => runBulkAction("suspend", [...selected])}>
            {t("admin.users.action.suspend")}
          </AdminButton>
          <select
            aria-label={t("admin.users.bulk.role_label")}
            defaultValue=""
            onChange={(e) => {
              const value = e.target.value;
              e.target.value = "";
              if (value === "admin") runBulkAction("promote", [...selected]);
              else if (value === "user") runBulkAction("demote", [...selected]);
            }}
          >
            <option value="" disabled>
              {t("admin.users.bulk.role_label")}
            </option>
            <option value="admin">{t("account.role.admin")}</option>
            <option value="user">{t("account.role.user")}</option>
          </select>
          <AdminButton variant="danger" onClick={() => handleBulkDelete([...selected])}>
            {t("admin.users.action.delete")}
          </AdminButton>
          <AdminButton variant="secondary" onClick={() => setSelected(new Set())}>
            {t("admin.users.bulk.clear")}
          </AdminButton>
        </div>
      )}
      {undo && (
        <div
          role="status"
          style={{
            position: "fixed",
            left: 24,
            bottom: 24,
            background: "var(--text-primary)",
            color: "var(--surface-1)",
            borderRadius: 6,
            padding: "8px 14px",
            display: "flex",
            gap: 12,
            alignItems: "center",
            fontSize: 13,
            zIndex: 40,
            boxShadow: "0 4px 16px rgba(0,0,0,0.16)",
          }}
        >
          <span>{undo.message}</span>
          <button
            type="button"
            onClick={handleUndo}
            style={{ color: "var(--accent)", fontWeight: 600, background: "none", border: "none", cursor: "pointer" }}
          >
            {t("admin.users.bulk.undo")}
          </button>
        </div>
      )}
    </div>
  );
}
