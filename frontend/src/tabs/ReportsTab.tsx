import { useNavigate, useParams } from "react-router-dom";
import { useReport, useReports } from "../api/hooks";
import { ctxToQueryString, useRangeContext } from "../api/rangeContext";
import type { TrendDay } from "../api/types";
import { relativeTime } from "../utils/relativeTime";
import { TabFilterBar } from "../components/TabFilterBar";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { Skeleton } from "../components/Skeleton";
import { DailyChart } from "../components/charts/DailyChart";
import { HourlyHeatmap, type HourlyCell } from "../components/charts/HourlyHeatmap";
import { ReportTable } from "../components/ReportTable";

const REPORT_LABEL: Record<string, string> = {
  ranking: "遅延ランキング",
  ranking_best: "定時運行ランキング",
  on_time: "定時率",
  worst_5min: "5分以上遅延",
  trend: "トレンド",
  compare_ranking: "比較ランキング",
  dow_weekday: "平日傾向",
  dow_weekend: "週末傾向",
};

export function ReportsTab() {
  const { agencyId, reportType } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const navigate = useNavigate();
  const [ctx] = useRangeContext();
  // Build the filter querystring from ctx so navigating between reports
  // carries only the filter dimensions — not unrelated keys like ?admin=1.
  const filterQS = ctxToQueryString(ctx);
  const filterSuffix = filterQS ? `?${filterQS}` : "";
  const list = useReports(id);
  const detail = useReport(id, reportType ?? null, ctx);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <TabFilterBar />
      <div style={{ display: "flex", gap: 16, flex: 1, minHeight: 0 }}>
      <div style={{ width: 280, flexShrink: 0 }}>
        <h3 style={{ marginTop: 0, fontSize: 14, color: "var(--text-secondary)" }}>レポート一覧</h3>
        {list.error && <ErrorBanner error={list.error} onRetry={() => list.refetch()} />}
        {list.isLoading && [...Array(6)].map((_, i) => (
          <Skeleton key={i} height={48} style={{ marginBottom: 6 }} />
        ))}
        {list.data && list.data.length === 0 && (
          <EmptyState
            title="まだレポートがありません"
            hint="集計を準備しています。次の更新までお待ちください。"
          />
        )}
        {list.data?.map((r) => {
          const active = r.report_type === reportType;
          return (
            <div
              key={r.report_type}
              onClick={() => navigate(`/agencies/${id}/reports/${r.report_type}${filterSuffix}`)}
              style={{
                padding: "10px 12px",
                marginBottom: 4,
                background: active ? "var(--accent-soft)" : "var(--bg-surface)",
                border: "1px solid var(--border-soft)",
                borderRadius: "var(--radius)",
                cursor: "pointer",
              }}
            >
              <div style={{ fontWeight: 500 }}>{REPORT_LABEL[r.report_type] ?? r.report_type}</div>
              <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
                {relativeTime(r.rendered_at)}
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        {!reportType && (
          <EmptyState title="レポートを選択してください" />
        )}
        {reportType && detail.error && (
          <ErrorBanner error={detail.error} onRetry={() => detail.refetch()} />
        )}
        {reportType && detail.isLoading && <Skeleton height={400} />}
        {detail.data && (
          <div>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
              <h2 style={{ margin: 0 }}>{REPORT_LABEL[detail.data.report_type] ?? detail.data.report_type}</h2>
              {detail.data.report_type !== "trend" && (
                <a
                  href={`/api/${id}/reports/${detail.data.report_type}?${new URLSearchParams({
                    from: ctx.from,
                    to: ctx.to,
                    ...(ctx.dow !== "all" ? { dow: ctx.dow } : {}),
                    ...(ctx.time_band !== "all" ? { time_band: ctx.time_band } : {}),
                    ...(ctx.service !== "all" ? { service: ctx.service } : {}),
                    ...(ctx.routes.length > 0 ? { routes: ctx.routes.join(",") } : {}),
                    format: "csv",
                  }).toString()}`}
                  download
                  style={{
                    fontSize: 12,
                    padding: "4px 12px",
                    background: "transparent",
                    border: "1px solid var(--border-subtle)",
                    borderRadius: 4,
                    color: "var(--text-secondary)",
                    textDecoration: "none",
                  }}
                >
                  ⬇ CSV
                </a>
              )}
            </div>
            <div style={{ color: "var(--text-tertiary)", fontSize: 13, margin: "8px 0 16px" }}>
              生成: {relativeTime(detail.data.rendered_at)}
              {detail.data.ctx && (
                <>
                  {" "}・ 期間: {detail.data.ctx.from} 〜 {detail.data.ctx.to}
                </>
              )}
            </div>
            {detail.data.report_type === "trend" ? (
              <TrendBlock data={detail.data.rows as unknown as { days: TrendDay[]; hourly: HourlyCell[] }[]} />
            ) : detail.data.rows.length > 0 ? (
              <ReportTable
                reportType={detail.data.report_type}
                rows={detail.data.rows as unknown[][]}
              />
            ) : (
              <EmptyState title="該当データがありません" hint="期間や条件を変更してください" />
            )}
            {detail.data.report_type !== "trend" && detail.data.rows.length > 0 && (
              <details style={{ marginTop: 16, color: "var(--text-tertiary)" }}>
                <summary style={{ cursor: "pointer", fontSize: 12 }}>
                  原文 ({detail.data.rows.length}件)
                </summary>
                <pre
                  style={{
                    background: "var(--bg-surface)",
                    border: "1px solid var(--border-soft)",
                    borderRadius: "var(--radius)",
                    padding: 12,
                    marginTop: 8,
                    whiteSpace: "pre-wrap",
                    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                    fontSize: 12,
                    lineHeight: 1.6,
                    maxWidth: 920,
                  }}
                >
                  {detail.data.text}
                </pre>
              </details>
            )}
          </div>
        )}
      </div>
      </div>
    </div>
  );
}

function TrendBlock({ data }: { data: { days: TrendDay[]; hourly: HourlyCell[] }[] }) {
  const payload = data[0] ?? { days: [], hourly: [] };
  return (
    <div>
      <DailyChart days={payload.days} />
      <HourlyHeatmap cells={payload.hourly} />
    </div>
  );
}

