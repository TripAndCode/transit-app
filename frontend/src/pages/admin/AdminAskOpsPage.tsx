import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { DataTable, type DataTableColumn } from "../../components/admin/DataTable";
import { ErrorBanner } from "../../components/ErrorBanner";
import {
  useAdminAskEval,
  useAdminAskFunnel,
  useAdminAskQueries,
  usePromoteAskQuery,
  type AskQueryLogRow,
  type AskRoute,
} from "../../api/admin";
import { formatDateTime } from "../../utils/format";
import { AdminButton, StatusChip } from "./adminControls";

const ROUTE_ORDER: readonly AskRoute[] = ["rules", "nn", "rag", "no_history"];
const CACHE_OUTCOMES = new Set(["hit", "miss", "bypass"]);

type AskFilters = { route?: string; status?: string; from?: string; to?: string };

function FunnelBar({
  route,
  count,
  successCount,
  maxCount,
}: {
  route: AskRoute;
  count: number;
  successCount: number;
  maxCount: number;
}) {
  const { t } = useTranslation();
  const widthPct = maxCount > 0 ? Math.round((count / maxCount) * 100) : 0;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
      <div style={{ width: 90, fontSize: 12, color: "var(--text-secondary)" }}>
        {t(`admin.ask_ops.route.${route}`)}
      </div>
      <div style={{ flex: 1, background: "var(--surface-2)", borderRadius: 4, height: 14, position: "relative" }}>
        <div
          style={{
            width: `${widthPct}%`,
            background: "var(--accent-soft)",
            height: "100%",
            borderRadius: 4,
          }}
        />
      </div>
      <div style={{ width: 110, fontSize: 12, color: "var(--text-tertiary)", textAlign: "right" }}>
        {t("admin.ask_ops.funnel.success_of", { success: successCount, count })}
      </div>
    </div>
  );
}

/** Owns cursor-stack pagination for one fixed filter set. Remounted (via a
 * `key` on the filter values) whenever a filter changes, which resets
 * pagination back to the first page without a synchronization effect. */
function AskQueryTable({ filters }: { filters: AskFilters }) {
  const { t } = useTranslation();
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const pageIndex = cursorStack.length - 1;
  const { data, isLoading, isPlaceholderData, error } = useAdminAskQueries({
    ...filters,
    cursor: cursorStack[pageIndex],
  });
  const promote = usePromoteAskQuery();
  /** The row a promotion is in flight for. Only that row's button goes
   *  pending — one slow embedding must not disable every other row's. */
  const [promotingId, setPromotingId] = useState<number | null>(null);
  const [promoteMessage, setPromoteMessage] = useState<string | null>(null);

  async function handlePromote(queryLogId: number) {
    setPromoteMessage(null);
    setPromotingId(queryLogId);
    try {
      const result = await promote.mutateAsync(queryLogId);
      if (result.promoted) {
        setPromoteMessage(t("admin.ask_ops.promote_success"));
      } else if (result.reason === "already_promoted") {
        setPromoteMessage(t("admin.ask_ops.promote_already"));
      } else {
        setPromoteMessage(t("admin.ask_ops.promote_not_eligible"));
      }
    } catch {
      setPromoteMessage(t("admin.ask_ops.promote_error"));
    } finally {
      setPromotingId(null);
    }
  }

  const columns: DataTableColumn<AskQueryLogRow>[] = [
    { key: "agency", header: t("admin.ask_ops.col.agency"), render: (row) => row.agency_name },
    {
      key: "question",
      header: t("admin.ask_ops.col.question"),
      render: (row) => (
        <span
          title={row.question}
          style={{ display: "block", maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        >
          {row.question}
        </span>
      ),
    },
    {
      key: "route",
      header: t("admin.ask_ops.col.route"),
      render: (row) => <StatusChip tone="neutral">{t(`admin.ask_ops.route.${row.route}`)}</StatusChip>,
    },
    {
      key: "status",
      header: t("admin.ask_ops.col.status"),
      render: (row) => (
        <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap" }}>
          <StatusChip tone={row.status === "ok" ? "good" : "warn"}>
            {t(`admin.ask_ops.status.${row.status}`)}
          </StatusChip>
          {row.numeric_guard_triggered && (
            <StatusChip tone="neutral">{t("admin.ask_ops.numeric_guard")}</StatusChip>
          )}
        </span>
      ),
    },
    { key: "tool", header: t("admin.ask_ops.col.tool"), render: (row) => row.tool ?? "—" },
    {
      key: "cache",
      header: t("admin.ask_ops.col.cache"),
      // A cache_outcome this build has no label for is shown verbatim rather
      // than as a raw translation key.
      render: (row) =>
        row.cache_outcome === null
          ? "—"
          : CACHE_OUTCOMES.has(row.cache_outcome)
            ? t(`admin.ask_ops.cache_outcome.${row.cache_outcome}`)
            : row.cache_outcome,
    },
    { key: "created", header: t("admin.ask_ops.col.created"), render: (row) => formatDateTime(row.created_at) },
    {
      key: "promote",
      header: "",
      align: "right",
      render: (row) =>
        row.promotable ? (
          <AdminButton
            variant="secondary"
            disabled={promotingId !== null}
            onClick={() => handlePromote(row.id)}
          >
            {t("admin.ask_ops.promote_action")}
          </AdminButton>
        ) : null,
    },
  ];

  function goNext() {
    if (data?.next_cursor) setCursorStack((s) => [...s, data.next_cursor as string]);
  }
  function goPrev() {
    setCursorStack((s) => (s.length > 1 ? s.slice(0, -1) : s));
  }

  return (
    <>
      {error != null && <ErrorBanner error={error} />}
      {isLoading && <div>{t("common.loading")}</div>}
      {promoteMessage && (
        <div role="status" style={{ marginBottom: 12, fontSize: 13, color: "var(--text-secondary)" }}>
          {promoteMessage}
        </div>
      )}
      <DataTable
        caption={t("admin.ask_ops.table_label")}
        rows={data?.rows ?? []}
        columns={columns}
        rowKey={(row) => String(row.id)}
        emptyLabel={t("admin.ask_ops.empty")}
        pending={isPlaceholderData}
      />
      <div style={{ marginTop: 12, display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <AdminButton variant="secondary" disabled={pageIndex === 0} onClick={goPrev}>
          {t("admin.ask_ops.pagination.prev")}
        </AdminButton>
        <AdminButton variant="secondary" disabled={!data?.next_cursor} onClick={goNext}>
          {t("admin.ask_ops.pagination.next")}
        </AdminButton>
      </div>
    </>
  );
}

/** Admin: Ask operations — query log, route funnel, promote-to-intent-cache,
 * kill-switch pointer, and the latest weekly eval result. */
export function AdminAskOpsPage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters: AskFilters = {
    route: searchParams.get("route") ?? undefined,
    status: searchParams.get("status") ?? undefined,
    from: searchParams.get("from") ?? undefined,
    to: searchParams.get("to") ?? undefined,
  };
  const filterKey = JSON.stringify(filters);

  // The funnel answers "where did questions go in this window", so it takes
  // the date range but not the route/status filters -- narrowing it by route
  // would leave a funnel with one bar.
  const { data: funnel } = useAdminAskFunnel({ from: filters.from, to: filters.to });
  const { data: evalResult } = useAdminAskEval();

  const maxCount = Math.max(1, ...(funnel?.by_route.map((r) => r.count) ?? [1]));

  function setFilter(key: keyof AskFilters, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearchParams(next);
  }

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <h1 style={{ fontSize: 22, marginBottom: 20 }}>{t("admin.ask_ops.title")}</h1>

      <div style={{ display: "flex", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
        <select
          aria-label={t("admin.ask_ops.col.route")}
          value={filters.route ?? ""}
          onChange={(e) => setFilter("route", e.target.value)}
        >
          <option value="">{t("admin.ask_ops.filter.route_all")}</option>
          {ROUTE_ORDER.map((r) => (
            <option key={r} value={r}>
              {t(`admin.ask_ops.route.${r}`)}
            </option>
          ))}
        </select>
        <select
          aria-label={t("admin.ask_ops.col.status")}
          value={filters.status ?? ""}
          onChange={(e) => setFilter("status", e.target.value)}
        >
          <option value="">{t("admin.ask_ops.filter.status_all")}</option>
          <option value="ok">{t("admin.ask_ops.status.ok")}</option>
          <option value="error">{t("admin.ask_ops.status.error")}</option>
        </select>
        <label style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 6 }}>
          {t("admin.ask_ops.filter.from_label")}
          <input type="date" value={filters.from ?? ""} onChange={(e) => setFilter("from", e.target.value)} />
        </label>
        <label style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 6 }}>
          {t("admin.ask_ops.filter.to_label")}
          <input type="date" value={filters.to ?? ""} onChange={(e) => setFilter("to", e.target.value)} />
        </label>
      </div>

      <section
        style={{ marginBottom: 24, padding: 16, background: "var(--surface-1)", borderRadius: "var(--radius-lg)" }}
      >
        <h2 style={{ fontSize: 15, marginBottom: 12 }}>{t("admin.ask_ops.funnel.title")}</h2>
        {ROUTE_ORDER.map((r) => {
          const entry = funnel?.by_route.find((fr) => fr.route === r);
          return (
            <FunnelBar
              key={r}
              route={r}
              count={entry?.count ?? 0}
              successCount={entry?.success_count ?? 0}
              maxCount={maxCount}
            />
          );
        })}
        <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 8 }}>
          {t("admin.ask_ops.funnel.total", { count: funnel?.total ?? 0 })}
        </div>
        <p style={{ margin: "6px 0 0", fontSize: 12, color: "var(--text-tertiary)" }}>
          {t("admin.ask_ops.funnel.providers_not_tracked")}
        </p>
      </section>

      <section
        style={{ marginBottom: 24, padding: 16, background: "var(--surface-1)", borderRadius: "var(--radius-lg)" }}
      >
        <h2 style={{ fontSize: 15, marginBottom: 8 }}>{t("admin.ask_ops.kill_switch.title")}</h2>
        <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 8 }}>
          {t("admin.ask_ops.kill_switch.router_note")}
        </p>
        <Link to="/admin/flags" style={{ fontSize: 13 }}>
          {t("admin.ask_ops.kill_switch.flags_link")}
        </Link>
      </section>

      <section
        style={{ marginBottom: 24, padding: 16, background: "var(--surface-1)", borderRadius: "var(--radius-lg)" }}
      >
        <h2 style={{ fontSize: 15, marginBottom: 8 }}>{t("admin.ask_ops.eval.title")}</h2>
        {evalResult ? (
          <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>
            {evalResult.score !== null && (
              <span style={{ marginRight: 16 }}>
                {t("admin.ask_ops.eval.score_label")}: {evalResult.score}
              </span>
            )}
            {evalResult.generated_at !== null && (
              <span>
                {t("admin.ask_ops.eval.generated_at_label")}: {formatDateTime(evalResult.generated_at)}
              </span>
            )}
          </div>
        ) : (
          <StatusChip tone="neutral">{t("admin.ask_ops.eval.not_run")}</StatusChip>
        )}
      </section>

      <AskQueryTable key={filterKey} filters={filters} />
    </div>
  );
}
