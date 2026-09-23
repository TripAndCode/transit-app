import { useEffect, useEffectEvent, useRef, useState } from "react";
import { Link, Outlet, useNavigate, useSearchParams, type SetURLSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  useAdminUsers,
  useBulkPatchUsers,
  useDeleteUser,
  usePatchUser,
  type AdminUser,
  type UserPatchBody,
} from "../../api/admin";
import { useSession } from "../../api/auth";
import { ErrorBanner } from "../../components/ErrorBanner";
import { PageHeader } from "../../components/ui/PageHeader";
import { Z_INDEX } from "../../styles/zIndex";
import { AdminAvatar, AdminButton, AdminSearchInput, StatusChip } from "./adminControls";
import { DataTable, type DataTableColumn } from "../../components/admin/DataTable";
import { InviteDialog } from "./InviteDialog";
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
  const [inviteOpen, setInviteOpen] = useState(false);
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
  if (rowSetKey !== priorRowSetKey) {
    setPriorRowSetKey(rowSetKey);
    setSelected(new Set());
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
      (del.isPending && del.variables === uid) ||
      // A row in a bulk request that has not answered yet: a per-row action
      // fired now would commit alongside it, and whichever landed second
      // would win rather than whichever the operator asked for last. Read
      // the request's own ids, not the page selection -- the `a` shortcut
      // acts on one unselected row, and undo runs after the selection has
      // already been cleared.
      (bulkPatch.isPending && (bulkPatch.variables?.ids.includes(uid) ?? false))
    );
  }


  // DataTable speaks string keys; this page's ids are numeric.
  const selectedKeys = new Set([...selected].map(String));

  // The bulk bar acts on the whole selection, so any batch still in flight
  // disables all of it -- clicking twice would send the same ids again and
  // let whichever landed second decide the outcome.
  const bulkBusy = bulkPatch.isPending || del.isPending;

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

  function onRowKeyDown(e: React.KeyboardEvent<HTMLTableRowElement>, row: AdminUser) {
    if (e.key !== "a") return;
    // Approve acts on the selection when there is one, so the shortcut
    // matches what the bulk bar in front of the operator is offering.
    const ids = selected.size > 0 ? [...selected] : [row.user_id];
    e.preventDefault();
    runBulkAction("approve", ids);
  }

  // `/` has to reach the search box from anywhere on the page, so it stays a
  // document listener rather than a row's own key handling.
  const onKeyDown = useEffectEvent((e: KeyboardEvent) => {
    if (isTypingTarget(e.target) || e.key !== "/") return;
    e.preventDefault();
    searchInputRef.current?.focus();
  });

  useEffect(() => {
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  const savedViewChips = SAVED_VIEWS.map((view) => ({
    id: view,
    label: t(`admin.users.view.${view}`),
    count: view === "pending" && pendingCount > 0 ? pendingCount : undefined,
  }));

  const columns: DataTableColumn<AdminUser>[] = [
    {
      key: "email",
      header: t("admin.users.col.email"),
      render: (u) => (
        <>
          <AdminAvatar label={u.name || u.email} />
          <Link
            to={`/admin/users/${u.user_id}`}
            state={{ listSearch: searchParams.toString() }}
            onClick={(e) => e.stopPropagation()}
          >
            {u.email}
          </Link>
        </>
      ),
    },
    { key: "name", header: t("admin.users.col.name"), render: (u) => u.name ?? "-" },
    {
      key: "role",
      header: t("admin.users.col.role"),
      render: (u) => (
        <select
          value={u.role}
          disabled={isRowLocked(u.user_id)}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => handleRoleChange(u.user_id, u.email, e.target.value)}
        >
          <option value="user">{t("account.role.user")}</option>
          <option value="admin">{t("account.role.admin")}</option>
        </select>
      ),
    },
    {
      key: "status",
      header: t("admin.users.col.status"),
      render: (u) => (
        <StatusChip tone={u.suspended_at ? "warn" : "good"}>
          {u.suspended_at ? t("admin.users.status.suspended") : t("admin.users.status.active")}
        </StatusChip>
      ),
    },
    {
      key: "llm_approved",
      header: t("admin.users.col.llm_approved"),
      render: (u) => (
        <StatusChip tone={u.llm_approved ? "good" : "warn"}>
          {u.llm_approved ? t("admin.users.llm_approved.yes") : t("admin.users.llm_approved.no")}
        </StatusChip>
      ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (u) => (
        <span style={{ whiteSpace: "nowrap" }}>
          <AdminButton
            variant="secondary"
            disabled={isRowLocked(u.user_id)}
            onClick={(e) => {
              e.stopPropagation();
              handleLlmApprovedToggle(u.user_id, u.llm_approved);
            }}
            style={{ marginRight: 8 }}
          >
            {u.llm_approved ? t("admin.users.action.revoke_llm") : t("admin.users.action.approve_llm")}
          </AdminButton>
          <AdminButton
            variant="secondary"
            disabled={isRowLocked(u.user_id)}
            onClick={(e) => {
              e.stopPropagation();
              handleSuspendToggle(u.user_id, u.suspended_at);
            }}
            style={{ marginRight: 8 }}
          >
            {u.suspended_at ? t("admin.users.action.resume") : t("admin.users.action.suspend")}
          </AdminButton>
          <AdminButton
            variant="danger"
            disabled={isRowLocked(u.user_id)}
            onClick={(e) => {
              e.stopPropagation();
              handleDelete(u.user_id, u.email);
            }}
          >
            {t("admin.users.action.delete")}
          </AdminButton>
        </span>
      ),
    },
  ];

  return (
    // position: relative bounds the user-detail Drawer (rendered via the
    // nested users/:uid route below) to this page's content area, so it
    // docks inside the admin main area rather than covering the sidebar.
    <div style={{ padding: 24, position: "relative" }}>
      <PageHeader
        title={t("admin.users.title")}
        actions={
          <AdminButton variant="primary" onClick={() => setInviteOpen(true)}>
            {t("admin.invite.trigger")}
          </AdminButton>
        }
      />
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
      {error && <ErrorBanner error={error} onRetry={() => refetch()} />}
      {isLoading && <div>{t("common.loading")}</div>}
      <DataTable
        caption={t("admin.users.table_label")}
        rows={data?.users ?? []}
        columns={columns}
        rowKey={(u) => String(u.user_id)}
        rowLabel={(u) => u.email}
        selectable
        isRowSelectable={(u) => isSelectable(u.user_id)}
        selectedIds={selectedKeys}
        onSelectionChange={(next) => setSelected(new Set([...next].map(Number)))}
        onOpen={(u) => navigate(`/admin/users/${u.user_id}`, { state: { listSearch: searchParams.toString() } })}
        onRowKeyDown={onRowKeyDown}
        savedViews={savedViewChips}
        activeView={activeView}
        onSelectView={(id) => selectView(id as SavedView)}
        emptyLabel={t("admin.users.empty")}
      />
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
            zIndex: Z_INDEX.sticky,
          }}
        >
          <b>{t("admin.users.bulk.selected_count", { count: selected.size })}</b>
          <AdminButton
            variant="secondary"
            disabled={bulkBusy}
            onClick={() => runBulkAction("approve", [...selected])}
          >
            {t("admin.users.action.approve_llm")}
          </AdminButton>
          <AdminButton
            variant="secondary"
            disabled={bulkBusy}
            onClick={() => runBulkAction("suspend", [...selected])}
          >
            {t("admin.users.action.suspend")}
          </AdminButton>
          <select
            aria-label={t("admin.users.bulk.role_label")}
            disabled={bulkBusy}
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
          <AdminButton variant="danger" disabled={bulkBusy} onClick={() => handleBulkDelete([...selected])}>
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
            // Above the selection bar it shares a corner with: undoing is
            // the one action still worth taking while both are on screen.
            zIndex: Z_INDEX.toast,
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
      <InviteDialog open={inviteOpen} onClose={() => setInviteOpen(false)} />
      <Outlet />
    </div>
  );
}
