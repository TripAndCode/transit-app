import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { useTodayRouteSummary } from "../api/hooks";
import { useRouteNames } from "../api/useRouteNames";
import type { RouteSummary } from "../api/types";
import { delayColor } from "../styles/tokens";
import { relativeTime } from "../utils/relativeTime";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { InsightHint } from "../components/InsightHint";
import { Skeleton } from "../components/Skeleton";

type SortKey = "worst" | "avg" | "trips" | "name";

export function LiveTab() {
  const { t } = useTranslation();
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
  // Age measured against the fetch timestamp (react-query's dataUpdatedAt)
  // rather than Date.now() — render stays pure for the React Compiler, and
  // the 30s auto-refresh keeps the reference point current anyway.
  const stale = latest ? dataUpdatedAt - new Date(latest).getTime() > 60 * 60 * 1000 : false;

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
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
          {[...Array(6)].map((_, i) => <Skeleton key={i} height={120} />)}
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

      {cards.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
          {cards.map((c) => (
            <RouteCard key={`${c.route_code}|${c.service_type}`} card={c} formatRoute={routeNames.format} t={t} />
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

function RouteCard({
  card,
  formatRoute,
  t,
}: {
  card: RouteSummary;
  formatRoute: (rc: string) => string;
  t: TFunction;
}) {
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
          label={t("live.card.avg")}
          value={formatDelayMinutesRounded(card.avg_delay_sec, t)}
          fullPrecision={formatDelay(card.avg_delay_sec, t)}
          dotColor={delayColor(avgMin)}
        />
        <Stat
          label={t("live.card.max")}
          value={formatDelayMinutesRounded(card.worst_delay_sec, t)}
          fullPrecision={formatDelay(card.worst_delay_sec, t)}
          dotColor={delayColor(worstMin)}
        />
      </div>
      <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
        {t("live.card.trip_samples", { trips: card.trips_observed, samples: card.samples.toLocaleString() })}
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

function formatDelayMinutesRounded(seconds: number, t: TFunction): string {
  const minutes = Math.round(Math.abs(seconds) / 60);
  if (minutes === 0) return t("common.on_time");
  const sign = seconds < 0 ? "-" : "+";
  return t("common.unit_min_signed", { sign, value: minutes });
}

function formatDelay(seconds: number, t: TFunction): string {
  if (seconds === 0) return t("common.on_time");
  const sign = seconds < 0 ? "-" : "+";
  const abs = Math.abs(seconds);
  const m = Math.floor(abs / 60);
  const s = abs % 60;
  if (m === 0) return t("common.unit_sec_signed", { sign, value: s });
  return t("common.unit_min_sec_signed", { sign, m, s: s.toString().padStart(2, "0") });
}

function formatLocal(ts: number): string {
  // Date locale is intentionally left as ja-JP per plan v1 — switching it on
  // i18n.language is tracked under date locale handling (out of scope here).
  return new Date(ts).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
