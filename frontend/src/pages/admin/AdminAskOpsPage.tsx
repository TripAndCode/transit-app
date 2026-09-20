import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  useAdminAskEval,
  useAdminAskFunnel,
  useAdminAskQueries,
  usePromoteAskQuery,
  type AskRoute,
} from "../../api/admin";
import { formatApiError } from "../../api/client";
import { AdminButton, StatusChip } from "./adminControls";

const ROUTE_ORDER: readonly AskRoute[] = ["rules", "nn", "rag", "no_history"];

function RoutePill({ route, t }: { route: AskRoute; t: ReturnType<typeof useTranslation>["t"] }) {
  return <StatusChip tone="neutral">{t(`admin.ask_ops.route.${route}`)}</StatusChip>;
}

function StatusPill({ status, t }: { status: "ok" | "error"; t: ReturnType<typeof useTranslation>["t"] }) {
  return (
    <StatusChip tone={status === "ok" ? "good" : "warn"}>{t(`admin.ask_ops.status.${status}`)}</StatusChip>
  );
}

function FunnelBar({ route, count, successCount, maxCount, t }: {
  route: AskRoute;
  count: number;
  successCount: number;
  maxCount: number;
  t: ReturnType<typeof useTranslation>["t"];
}) {
  const widthPct = maxCount > 0 ? Math.round((count / maxCount) * 100) : 0;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
      <div style={{ width: 90, fontSize: 12, color: "var(--text-secondary)" }}>{t(`admin.ask_ops.route.${route}`)}</div>
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

/** Admin: Ask operations — query log, route funnel, promote-to-intent-cache,
 * kill-switch summary, and the latest weekly eval result. */
export function AdminAskOpsPage() {
  const { t } = useTranslation();
  const [route, setRoute] = useState("");
  const [status, setStatus] = useState("");
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const cursor = cursorStack[cursorStack.length - 1];

  const { data, isLoading, error } = useAdminAskQueries({
    route: route || undefined,
    status: status || undefined,
    cursor,
  });
  const { data: funnel } = useAdminAskFunnel({});
  const { data: evalResult } = useAdminAskEval();
  const promote = usePromoteAskQuery();
  const [promoteMessage, setPromoteMessage] = useState<string | null>(null);

  const maxCount = Math.max(1, ...(funnel?.by_route.map((r) => r.count) ?? [1]));

  async function handlePromote(queryLogId: number) {
    setPromoteMessage(null);
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
    }
  }

  function setFilter(kind: "route" | "status", value: string) {
    setCursorStack([null]);
    if (kind === "route") setRoute(value);
    else setStatus(value);
  }

  function goNext() {
    if (data?.next_cursor) setCursorStack((prev) => [...prev, data.next_cursor]);
  }

  function goPrev() {
    setCursorStack((prev) => (prev.length > 1 ? prev.slice(0, -1) : prev));
  }

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <h1 style={{ fontSize: 22, marginBottom: 20 }}>{t("admin.ask_ops.title")}</h1>

      {/* Route funnel + providers */}
      <section style={{ marginBottom: 24, padding: 16, background: "var(--surface-1)", borderRadius: "var(--radius-md)" }}>
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
              t={t}
            />
          );
        })}
        <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 8 }}>
          {t("admin.ask_ops.funnel.total", { count: funnel?.total ?? 0 })}
        </div>
        <div style={{ marginTop: 12, fontSize: 12, color: "var(--text-tertiary)" }}>
          <strong>{t("admin.ask_ops.funnel.providers_title")}:</strong>{" "}
          {t("admin.ask_ops.funnel.providers_not_tracked")}
        </div>
      </section>

      {/* Kill-switch summary */}
      <section style={{ marginBottom: 24, padding: 16, background: "var(--surface-1)", borderRadius: "var(--radius-md)" }}>
        <h2 style={{ fontSize: 15, marginBottom: 8 }}>{t("admin.ask_ops.kill_switch.title")}</h2>
        <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 8 }}>
          {t("admin.ask_ops.kill_switch.router_note")}
        </p>
        <Link to="/admin/flags" style={{ fontSize: 13 }}>
          {t("admin.ask_ops.kill_switch.flags_link")}
        </Link>
      </section>

      {/* Weekly eval */}
      <section style={{ marginBottom: 24, padding: 16, background: "var(--surface-1)", borderRadius: "var(--radius-md)" }}>
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
                {t("admin.ask_ops.eval.generated_at_label")}: {evalResult.generated_at}
              </span>
            )}
          </div>
        ) : (
          <StatusChip tone="neutral">{t("admin.ask_ops.eval.not_run")}</StatusChip>
        )}
      </section>

      {/* Filters */}
      <div style={{ display: "flex", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
        <select
          aria-label={t("admin.ask_ops.col.route")}
          value={route}
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
          value={status}
          onChange={(e) => setFilter("status", e.target.value)}
        >
          <option value="">{t("admin.ask_ops.filter.status_all")}</option>
          <option value="ok">{t("admin.ask_ops.status.ok")}</option>
          <option value="error">{t("admin.ask_ops.status.error")}</option>
        </select>
      </div>

      {error && <div style={{ color: "var(--text-tertiary)" }}>{formatApiError(error)}</div>}
      {isLoading && <div>{t("common.loading")}</div>}
      {promoteMessage && (
        <div role="status" style={{ marginBottom: 12, fontSize: 13, color: "var(--text-secondary)" }}>
          {promoteMessage}
        </div>
      )}

      <table className="admin-table">
        <thead>
          <tr>
            <th>{t("admin.ask_ops.col.agency")}</th>
            <th>{t("admin.ask_ops.col.question")}</th>
            <th>{t("admin.ask_ops.col.route")}</th>
            <th>{t("admin.ask_ops.col.status")}</th>
            <th>{t("admin.ask_ops.col.tool")}</th>
            <th>{t("admin.ask_ops.col.cache")}</th>
            <th>{t("admin.ask_ops.col.created")}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {data && data.rows.length === 0 && (
            <tr>
              <td colSpan={8} style={{ textAlign: "center", color: "var(--text-tertiary)", padding: 24 }}>
                {t("admin.ask_ops.empty")}
              </td>
            </tr>
          )}
          {data?.rows.map((row) => (
            <tr key={row.id}>
              <td>{row.agency_name}</td>
              <td style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {row.question}
              </td>
              <td>
                <RoutePill route={row.route} t={t} />
              </td>
              <td>
                <StatusPill status={row.status} t={t} />
              </td>
              <td style={{ color: "var(--text-tertiary)", fontSize: 13 }}>{row.tool ?? "-"}</td>
              <td style={{ color: "var(--text-tertiary)", fontSize: 13 }}>
                {row.cache_outcome ? t(`admin.ask_ops.cache_outcome.${row.cache_outcome}`) : "-"}
              </td>
              <td style={{ color: "var(--text-tertiary)", fontSize: 13 }}>{row.created_at}</td>
              <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                {row.promotable && (
                  <AdminButton
                    variant="secondary"
                    disabled={promote.isPending}
                    onClick={() => handlePromote(row.id)}
                  >
                    {t("admin.ask_ops.promote_action")}
                  </AdminButton>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={{ marginTop: 12, display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <AdminButton variant="secondary" disabled={cursorStack.length <= 1} onClick={goPrev}>
          {t("admin.ask_ops.pagination.prev")}
        </AdminButton>
        <AdminButton variant="secondary" disabled={!data?.next_cursor} onClick={goNext}>
          {t("admin.ask_ops.pagination.next")}
        </AdminButton>
      </div>
    </div>
  );
}
