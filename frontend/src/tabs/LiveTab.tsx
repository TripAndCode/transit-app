import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useTodayRouteSummary } from "../api/hooks";
import { useRouteNames } from "../api/useRouteNames";
import type { RouteSummary } from "../api/types";
import { delayColor } from "../styles/tokens";
import { relativeTime } from "../utils/relativeTime";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { Skeleton } from "../components/Skeleton";

type SortKey = "worst" | "avg" | "trips" | "name";

export function LiveTab() {
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const [autoRefresh, setAutoRefresh] = useState(true);
  const { data, isLoading, error, refetch, dataUpdatedAt, isFetching } = useTodayRouteSummary(id, {
    autoRefresh,
  });
  const routeNames = useRouteNames(id);
  const [sort, setSort] = useState<SortKey>("worst");
  const [filter, setFilter] = useState("");

  const cards = useMemo<RouteSummary[]>(() => {
    if (!data?.routes) return [];
    const filtered = filter.trim()
      ? data.routes.filter((r) => {
          const name = routeNames.format(r.route_code).toLowerCase();
          const q = filter.trim().toLowerCase();
          return name.includes(q) || r.route_code.includes(q);
        })
      : data.routes;
    const sorted = [...filtered].sort((a, b) => {
      if (sort === "worst") return b.worst_delay_sec - a.worst_delay_sec;
      if (sort === "avg") return b.avg_delay_sec - a.avg_delay_sec;
      if (sort === "trips") return b.trips_observed - a.trips_observed;
      return routeNames.format(a.route_code).localeCompare(routeNames.format(b.route_code));
    });
    return sorted;
  }, [data, filter, sort, routeNames]);

  const latest = data?.latest_captured_at;
  const stale = latest ? Date.now() - new Date(latest).getTime() > 60 * 60 * 1000 : false;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 16, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>
          最新観測 {data?.date && <span style={{ color: "var(--text-tertiary)", fontSize: 14 }}>({data.date})</span>}
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
            最終観測: {relativeTime(latest)}
          </span>
        )}
        <label style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--text-secondary)", marginLeft: "auto" }}>
          <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
          自動更新 (30秒)
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
          手動更新
        </button>
      </div>

      <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="系統で絞り込み"
          style={{ flex: "1 1 240px", maxWidth: 320 }}
        />
        <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>並び順</span>
        <SortPill active={sort === "worst"} onClick={() => setSort("worst")}>最大遅延</SortPill>
        <SortPill active={sort === "avg"} onClick={() => setSort("avg")}>平均遅延</SortPill>
        <SortPill active={sort === "trips"} onClick={() => setSort("trips")}>運行便数</SortPill>
        <SortPill active={sort === "name"} onClick={() => setSort("name")}>名前順</SortPill>
        {data && (
          <span style={{ fontSize: 12, color: "var(--text-tertiary)", marginLeft: "auto" }}>
            {isFetching ? "更新中..." : dataUpdatedAt ? `更新: ${formatLocal(dataUpdatedAt)}` : ""}
          </span>
        )}
      </div>

      {error && <ErrorBanner error={error} onRetry={() => refetch()} />}
      {isLoading && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
          {[...Array(6)].map((_, i) => <Skeleton key={i} height={120} />)}
        </div>
      )}
      {data?.routes && data.routes.length === 0 && (
        <EmptyState
          title={
            data.latest_captured_at
              ? `まだ表示できる観測がありません (最終受信: ${relativeTime(data.latest_captured_at)})`
              : "まだ表示できる観測がありません"
          }
          hint="次の取り込みを待っています。数分後に自動で更新されます。"
        />
      )}

      {cards.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
          {cards.map((c) => (
            <RouteCard key={`${c.route_code}|${c.service_type}`} card={c} formatRoute={routeNames.format} />
          ))}
        </div>
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

function RouteCard({ card, formatRoute }: { card: RouteSummary; formatRoute: (rc: string) => string }) {
  const avgMin = card.avg_delay_sec / 60;
  const worstMin = card.worst_delay_sec / 60;
  return (
    <div
      style={{
        background: "var(--bg-surface)",
        border: "1px solid var(--border-soft)",
        borderRadius: "var(--radius-lg)",
        padding: 14,
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <span style={{ fontWeight: 600, fontSize: 14, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {formatRoute(card.route_code)}
        </span>
        {card.service_type && (
          <span style={{ fontSize: 11, color: "var(--text-tertiary)", whiteSpace: "nowrap" }}>{card.service_type}</span>
        )}
      </div>
      <div style={{ display: "flex", gap: 12 }}>
        <Stat
          label="平均"
          value={formatDelayMinutesRounded(card.avg_delay_sec)}
          fullPrecision={formatDelay(card.avg_delay_sec)}
          dotColor={delayColor(avgMin)}
        />
        <Stat
          label="最大"
          value={formatDelayMinutesRounded(card.worst_delay_sec)}
          fullPrecision={formatDelay(card.worst_delay_sec)}
          dotColor={delayColor(worstMin)}
        />
      </div>
      <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
        {card.trips_observed} 便 / {card.samples.toLocaleString()} 観測
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  fullPrecision,
  dotColor,
}: {
  label: string;
  value: string;
  fullPrecision: string;
  dotColor: string;
}) {
  return (
    <div title={fullPrecision}>
      <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 2 }}>
        {label}
      </div>
      <div
        style={{
          fontSize: 16,
          fontWeight: 600,
          color: "var(--text-primary)",
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <span aria-hidden="true" style={{ color: dotColor, fontSize: 10, lineHeight: 1 }}>
          ●
        </span>
        <span>{value}</span>
      </div>
    </div>
  );
}

function formatDelayMinutesRounded(seconds: number): string {
  if (seconds === 0) return "定刻";
  const sign = seconds < 0 ? "-" : "+";
  const minutes = Math.round(Math.abs(seconds) / 60);
  return `${sign}${minutes}分`;
}

function formatDelay(seconds: number): string {
  if (seconds === 0) return "定刻";
  const sign = seconds < 0 ? "-" : "+";
  const abs = Math.abs(seconds);
  const m = Math.floor(abs / 60);
  const s = abs % 60;
  if (m === 0) return `${sign}${s}秒`;
  return `${sign}${m}分${s.toString().padStart(2, "0")}秒`;
}

function formatLocal(ts: number): string {
  return new Date(ts).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
