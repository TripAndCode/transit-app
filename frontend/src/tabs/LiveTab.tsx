/**
 * 最新観測 tab — baseline-relative triage list for the latest observation day.
 *
 * Fetches the per-route summary, groups routes into severity buckets
 * (anomaly / watch / normal / no_baseline) ranked by deviation from each
 * route's historical baseline, and opens a per-route drilldown
 * ({@link RouteDrilldown}) on row click. Bucket order is fixed; the sort pills
 * only reorder rows within a bucket.
 */
import { useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useTodayRouteSummary } from "../api/hooks";
import { useRouteNames } from "../api/useRouteNames";
import type { RouteSummary } from "../api/types";
import { relativeTime } from "../utils/relativeTime";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { InsightHint } from "../components/InsightHint";
import { Skeleton } from "../components/Skeleton";
import { groupByBucket, type BucketGroup } from "./live/bucket";
import { RouteRow } from "./live/RouteRow";
import { RouteDrilldown } from "./live/RouteDrilldown";

type SortKey = "deviation" | "worst" | "avg" | "trips" | "name";

/** Sort a group's routes by the given non-deviation key. Returns a new array. */
function sortRoutes(routes: RouteSummary[], sort: Exclude<SortKey, "deviation">, formatRoute: (rc: string) => string): RouteSummary[] {
  const copy = [...routes];
  copy.sort((a, b) => {
    if (sort === "worst") return b.worst_delay_sec - a.worst_delay_sec;
    if (sort === "avg") return b.avg_delay_sec - a.avg_delay_sec;
    if (sort === "trips") return b.trips_observed - a.trips_observed;
    // name
    return formatRoute(a.route_code).localeCompare(formatRoute(b.route_code));
  });
  return copy;
}

export function LiveTab() {
  const { t } = useTranslation();
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const [autoRefresh, setAutoRefresh] = useState(true);
  const { data, isLoading, error, refetch, dataUpdatedAt, isFetching } = useTodayRouteSummary(id, {
    autoRefresh,
  });
  const routeNames = useRouteNames(id);
  const [sort, setSort] = useState<SortKey>("deviation");
  const [filter, setFilter] = useState("");
  const [openRoute, setOpenRoute] = useState<RouteSummary | null>(null);

  const latest = data?.latest_captured_at;
  const stale = latest ? dataUpdatedAt - new Date(latest).getTime() > 60 * 60 * 1000 : false;

  const filtered: RouteSummary[] = data?.routes
    ? filter.trim()
      ? data.routes.filter((r) => {
          const name = routeNames.format(r.route_code).toLowerCase();
          const q = filter.trim().toLowerCase();
          return name.includes(q) || r.route_code.includes(q);
        })
      : data.routes
    : [];

  const rawGroups = groupByBucket(filtered);
  const groups: BucketGroup[] =
    sort === "deviation"
      ? rawGroups
      : rawGroups.map((g) => ({ ...g, routes: sortRoutes(g.routes, sort, routeNames.format) }));

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 16, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 18, display: "inline-flex", alignItems: "center", gap: 6 }}>
          {t("live.title")} {data?.date && <span style={{ color: "var(--text-tertiary)", fontSize: 14 }}>({data.date})</span>}
          <InsightHint
            title={t("live.hint.title")}
            body={
              <>
                {t("live.hint.body_1_intro")}<strong>{t("live.hint.avg_strong")}</strong>{t("live.hint.avg_meaning")}
                <strong>{t("live.hint.max_strong")}</strong>{t("live.hint.max_meaning")}
                {t("live.hint.body_2")}
                {t("live.hint.body_3")}
              </>
            }
          />
        </h2>
        {latest && (
          <span
            style={{
              fontSize: 12,
              padding: "2px 10px",
              borderRadius: 999,
              background: stale ? "var(--error-bg)" : "var(--accent-soft)",
              color: stale ? "var(--error-fg)" : "var(--accent)",
            }}
          >
            {t("live.last_observation", { when: relativeTime(latest) })}
          </span>
        )}
        <label style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--text-secondary)", marginLeft: "auto" }}>
          <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
          {t("live.auto_refresh")}
        </label>
        <button
          type="button"
          onClick={() => refetch()}
          style={{
            background: "transparent",
            border: "1px solid var(--border-subtle)",
            padding: "4px 12px",
            borderRadius: 4,
            fontSize: 13,
          }}
        >
          {t("live.manual_refresh")}
        </button>
      </div>

      <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={t("live.filter_placeholder")}
          style={{ flex: "1 1 240px", maxWidth: 320 }}
        />
        <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{t("live.sort_label")}</span>
        <SortPill active={sort === "deviation"} onClick={() => setSort("deviation")}>{t("live.sort.deviation")}</SortPill>
        <SortPill active={sort === "worst"} onClick={() => setSort("worst")}>{t("live.sort.worst")}</SortPill>
        <SortPill active={sort === "avg"} onClick={() => setSort("avg")}>{t("live.sort.avg")}</SortPill>
        <SortPill active={sort === "trips"} onClick={() => setSort("trips")}>{t("live.sort.trips")}</SortPill>
        <SortPill active={sort === "name"} onClick={() => setSort("name")}>{t("live.sort.name")}</SortPill>
        {data && (
          <span style={{ fontSize: 12, color: "var(--text-tertiary)", marginLeft: "auto" }}>
            {isFetching
              ? t("live.updating")
              : dataUpdatedAt
                ? t("live.updated_at", { time: formatLocal(dataUpdatedAt) })
                : ""}
          </span>
        )}
      </div>

      {error && <ErrorBanner error={error} onRetry={() => refetch()} />}
      {isLoading && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {[...Array(6)].map((_, i) => <Skeleton key={i} height={44} />)}
        </div>
      )}
      {data?.routes && data.routes.length === 0 && (
        <EmptyState
          title={
            data.latest_captured_at
              ? t("live.empty.with_last", { when: relativeTime(data.latest_captured_at) })
              : t("live.empty.title")
          }
          hint={t("live.empty.hint")}
        />
      )}

      {!isLoading && groups.map((g) => {
        if (g.routes.length === 0) return null;
        const expanded = g.bucket === "anomaly" || g.bucket === "watch";
        const heading = (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "6px 10px",
              background: "var(--bg-muted, #faf7f2)",
              borderRadius: 4,
              marginBottom: 2,
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            <span>{t(`live.bucket.${g.bucket}`)}</span>
            <span style={{ fontWeight: 400, color: "var(--text-tertiary)" }}>
              {t("live.bucket.count", { count: g.routes.length })}
            </span>
          </div>
        );

        const rows = g.routes.map((r) => (
          <RouteRow
            key={`${r.route_code}|${r.service_type}`}
            route={r}
            formatRoute={routeNames.format}
            onOpen={setOpenRoute}
            t={t}
          />
        ));

        if (expanded) {
          return (
            <section key={g.bucket} style={{ marginBottom: 16 }}>
              {heading}
              {rows}
            </section>
          );
        }

        return (
          <details key={g.bucket} style={{ marginBottom: 16 }}>
            <summary style={{ listStyle: "none", cursor: "pointer" }}>
              {heading}
            </summary>
            {rows}
          </details>
        );
      })}

      {openRoute && id != null && (
        <RouteDrilldown
          agencyId={id}
          routeCode={openRoute.route_code}
          routeName={routeNames.format(openRoute.route_code)}
          onClose={() => setOpenRoute(null)}
          t={t}
        />
      )}
    </div>
  );
}

function SortPill({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        background: active ? "var(--accent-soft)" : "var(--bg-surface)",
        color: active ? "var(--accent)" : "var(--text-secondary)",
        border: `1px solid ${active ? "var(--accent)" : "var(--border-subtle)"}`,
        borderRadius: 999,
        padding: "4px 12px",
        fontSize: 12,
        cursor: "pointer",
      }}
    >
      {children}
    </button>
  );
}

function formatLocal(ts: number): string {
  // Date locale is intentionally left as ja-JP per plan v1 — switching it on
  // i18n.language is tracked under date locale handling (out of scope here).
  return new Date(ts).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
