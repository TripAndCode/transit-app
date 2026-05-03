import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useLiveDelays } from "../api/hooks";
import type { LiveDelay } from "../api/types";
import { delayColor } from "../styles/tokens";
import { relativeTime } from "../utils/relativeTime";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { Skeleton } from "../components/Skeleton";

type SortKey = keyof Pick<LiveDelay, "route_code" | "service_type" | "scheduled_time" | "dep_delay" | "captured_at">;

export function LiveTab() {
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const [autoRefresh, setAutoRefresh] = useState(true);
  const { data, isLoading, error, refetch, dataUpdatedAt, isFetching } =
    useLiveDelays(id, { autoRefresh });
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({
    key: "dep_delay",
    dir: "desc",
  });

  const rows = useMemo(() => {
    if (!data) return [];
    const sorted = [...data].sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (av < bv) return sort.dir === "asc" ? -1 : 1;
      if (av > bv) return sort.dir === "asc" ? 1 : -1;
      return 0;
    });
    return sorted;
  }, [data, sort]);

  function toggleSort(key: SortKey) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" }));
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 16 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--text-secondary)" }}>
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
          />
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
          }}
        >
          手動更新
        </button>
        <span style={{ color: "var(--text-tertiary)", fontSize: 13 }}>
          {isFetching ? "更新中..." : dataUpdatedAt ? `最終更新: ${formatTime(dataUpdatedAt)}` : ""}
        </span>
      </div>

      {error && <ErrorBanner error={error} onRetry={() => refetch()} />}
      {isLoading && (
        <div>
          {[...Array(6)].map((_, i) => <Skeleton key={i} height={32} style={{ margin: "6px 0" }} />)}
        </div>
      )}
      {data && data.length === 0 && <EmptyState title="リアルタイムデータがありません" />}

      {data && data.length > 0 && (
        <table style={{ width: "100%", borderCollapse: "collapse", background: "var(--bg-surface)", borderRadius: "var(--radius)", overflow: "hidden", border: "1px solid var(--border-soft)" }}>
          <thead>
            <tr style={{ background: "var(--bg-soft)", textAlign: "left" }}>
              <Th label="系統" k="route_code" sort={sort} onClick={toggleSort} />
              <Th label="種別" k="service_type" sort={sort} onClick={toggleSort} />
              <Th label="予定時刻" k="scheduled_time" sort={sort} onClick={toggleSort} />
              <Th label="遅延" k="dep_delay" sort={sort} onClick={toggleSort} />
              <Th label="観測時刻" k="captured_at" sort={sort} onClick={toggleSort} />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.trip_id}|${r.captured_at}`} style={{ borderTop: "1px solid var(--border-soft)" }}>
                <td style={td}>{r.route_code ?? "—"}</td>
                <td style={td}>{r.service_type ?? "—"}</td>
                <td style={td}>{r.scheduled_time ?? "—"}</td>
                <td style={{ ...td, color: delayColor(r.dep_delay / 60), fontWeight: 600 }}>
                  {formatDelay(r.dep_delay)}
                </td>
                <td style={{ ...td, color: "var(--text-secondary)" }}>{relativeTime(r.captured_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

const td: React.CSSProperties = { padding: "10px 14px", fontSize: 14 };

function Th({ label, k, sort, onClick }: { label: string; k: SortKey; sort: { key: SortKey; dir: "asc" | "desc" }; onClick: (k: SortKey) => void }) {
  const active = sort.key === k;
  return (
    <th scope="col" style={{ ...td, fontWeight: 500, color: "var(--text-secondary)", padding: 0 }}>
      <button
        type="button"
        onClick={() => onClick(k)}
        style={{
          width: "100%",
          textAlign: "left",
          background: "none",
          border: "none",
          padding: "10px 14px",
          font: "inherit",
          color: "inherit",
          cursor: "pointer",
          userSelect: "none",
        }}
      >
        {label} {active && (sort.dir === "asc" ? "▲" : "▼")}
      </button>
    </th>
  );
}

function formatDelay(seconds: number): string {
  if (seconds === 0) return "定刻";
  const sign = seconds < 0 ? "-" : "+";
  const abs = Math.abs(seconds);
  const m = Math.floor(abs / 60);
  const s = abs % 60;
  return `${sign}${m}分${s.toString().padStart(2, "0")}秒`;
}

function formatTime(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
