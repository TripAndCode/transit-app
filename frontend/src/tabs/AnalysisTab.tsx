import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useReport, useReports } from "../api/hooks";
import { ctxToQueryString, isoDaysAgo, todayISO, useRangeContext, type RangeCtx } from "../api/rangeContext";
import type { TrendDay } from "../api/types";
import { TabFilterBar } from "../components/TabFilterBar";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { InsightHint } from "../components/InsightHint";
import { InsightPanel } from "../components/InsightPanel";
import { Skeleton } from "../components/Skeleton";
import { DailyChart } from "../components/charts/DailyChart";
import { HourlyHeatmap, type HourlyCell } from "../components/charts/HourlyHeatmap";
import { BandGrid, Legend } from "../components/charts/DowBandGrid";
import { delayColor } from "../styles/tokens";
import type { Band, ForecastOverviewGridCell, ForecastOverviewWorst } from "../api/types";
import { ReportTable } from "../components/ReportTable";
import { RouteForecastSection } from "../components/RouteForecastSection";

/** "This week" = the 7 days ending today, in the ctx's from/to string
 *  format. Used by the "no data" EmptyState's recovery action to jump to a
 *  window likely to have real data, rather than leaving the user stuck on
 *  whatever empty range they'd filtered to. */
function thisWeekRange(): { from: string; to: string } {
  return { from: isoDaysAgo(6), to: todayISO() };
}

export function AnalysisTab() {
  const { t } = useTranslation();
  const { agencyId, reportType } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const navigate = useNavigate();
  const [ctx, update] = useRangeContext();
  // Build the filter querystring from ctx so navigating between reports
  // carries only the filter dimensions — not unrelated keys like ?admin=1.
  const filterQS = ctxToQueryString(ctx);
  const filterSuffix = filterQS ? `?${filterQS}` : "";
  const list = useReports(id);
  const detail = useReport(id, reportType && reportType !== "route_forecast" ? reportType : null, ctx);

  const reportLabels: Record<string, string> = {
    ranking: t("reports.type.ranking"),
    ranking_best: t("reports.type.ranking_best"),
    on_time: t("reports.type.on_time"),
    worst_5min: t("reports.type.worst_5min"),
    trend: t("reports.type.trend"),
    compare_ranking: t("reports.type.compare_ranking"),
    dow_weekday: t("reports.type.dow_weekday"),
    dow_weekend: t("reports.type.dow_weekend"),
    route_forecast: t("reports.type.route_forecast"),
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <TabFilterBar />
      <div style={{ display: "flex", gap: 16, flex: 1, minHeight: 0 }}>
      <div style={{ width: 280, flexShrink: 0 }}>
        <h3 style={{ marginTop: 0, fontSize: 14, color: "var(--text-secondary)", display: "inline-flex", alignItems: "center", gap: 6 }}>
          {t("reports.list_title")}
          <InsightHint
            title={t("reports.hint.title")}
            body={
              <>
                <strong>{t("reports.hint.ranking_strong")}</strong>{t("reports.hint.ranking_body")}
                <br /><br />
                <strong>{t("reports.hint.trend_strong")}</strong>{t("reports.hint.trend_body")}
                <br /><br />
                <strong>{t("reports.hint.heatmap_strong")}</strong>{t("reports.hint.heatmap_body")}
                <br /><br />
                <strong>{t("reports.hint.dow_strong")}</strong>{t("reports.hint.dow_body")}
                <br /><br />
                {t("reports.hint.csv")}
              </>
            }
          />
        </h3>
        {list.error && <ErrorBanner error={list.error} onRetry={() => list.refetch()} />}
        {list.isLoading && [...Array(6)].map((_, i) => (
          <Skeleton key={i} height={48} style={{ marginBottom: 6 }} />
        ))}
        {list.data && list.data.length === 0 && (
          <EmptyState
            title={t("reports.empty.title")}
            hint={t("reports.empty.hint")}
          />
        )}
        {list.data?.map((r) => {
          const active = r.report_type === reportType;
          return (
            <button
              key={r.report_type}
              type="button"
              onClick={() => navigate(`/agencies/${id}/analysis/${r.report_type}${filterSuffix}`)}
              aria-pressed={active}
              style={{
                appearance: "none",
                font: "inherit",
                textAlign: "left",
                color: "inherit",
                display: "block",
                width: "100%",
                padding: "10px 12px",
                marginBottom: 4,
                background: active ? "var(--accent-soft)" : "var(--bg-surface)",
                border: "1px solid var(--border-soft)",
                borderRadius: "var(--radius)",
                cursor: "pointer",
              }}
            >
              <div style={{ fontWeight: 500 }}>{reportLabels[r.report_type] ?? r.report_type}</div>
            </button>
          );
        })}
        <button
          type="button"
          onClick={() => navigate(`/agencies/${id}/analysis/route_forecast${filterSuffix}`)}
          aria-pressed={reportType === "route_forecast"}
          style={{
            appearance: "none",
            font: "inherit",
            textAlign: "left",
            color: "inherit",
            display: "block",
            width: "100%",
            padding: "10px 12px",
            marginBottom: 4,
            background: reportType === "route_forecast" ? "var(--accent-soft)" : "var(--bg-surface)",
            border: "1px solid var(--border-soft)",
            borderRadius: "var(--radius)",
            cursor: "pointer",
          }}
        >
          <div style={{ fontWeight: 500 }}>{reportLabels.route_forecast}</div>
        </button>
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        {!reportType && (
          <EmptyState title={t("reports.select_prompt")} />
        )}
        {reportType === "route_forecast" && id != null && (
          <div>
            <h2 style={{ margin: "0 0 16px" }}>{reportLabels.route_forecast}</h2>
            <RouteForecastSection aid={id} />
          </div>
        )}
        {reportType && reportType !== "route_forecast" && detail.error && (
          <ErrorBanner error={detail.error} onRetry={() => detail.refetch()} />
        )}
        {reportType && reportType !== "route_forecast" && detail.isFetching && <Skeleton height={400} />}
        {reportType !== "route_forecast" && detail.data && (
          <div>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
              <h2 style={{ margin: 0 }}>{reportLabels[detail.data.report_type] ?? detail.data.report_type}</h2>
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
            {detail.data.ctx && (
              <div style={{ color: "var(--text-tertiary)", fontSize: 13, margin: "8px 0 16px" }}>
                {t("reports.range_suffix", { from: detail.data.ctx.from, to: detail.data.ctx.to })}
              </div>
            )}
            {detail.data.report_type === "trend" ? (
              <TrendBlock
                data={
                  detail.data.rows as unknown as {
                    days: TrendDay[];
                    hourly: HourlyCell[];
                    dow_band: { grid: ForecastOverviewGridCell[]; worst: ForecastOverviewWorst | null };
                  }[]
                }
                ctx={ctx}
              />
            ) : detail.data.rows.length > 0 ? (
              <ReportTable
                reportType={detail.data.report_type}
                rows={detail.data.rows as unknown[][]}
              />
            ) : (
              <EmptyState
                title={t("reports.no_data.title")}
                hint={t("reports.no_data.hint")}
                action={{ label: t("reports.no_data.reset_action"), onClick: () => update(thisWeekRange()) }}
              />
            )}
            {detail.data.report_type !== "trend" && detail.data.rows.length > 0 && (
              <details style={{ marginTop: 16, color: "var(--text-tertiary)" }}>
                <summary style={{ cursor: "pointer", fontSize: 12 }}>
                  {t("reports.raw_rows", { count: detail.data.rows.length })}
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
      {/* key={id} forces a remount on agency switch -- InsightPanel's `seen`
          state is seeded once from sessionStorage per mount; without this,
          switching agencies would keep the previous agency's exclude set
          in React state even though its sessionStorage key is now separate. */}
      <InsightPanel key={id} />
      </div>
    </div>
  );
}

const WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;

function TrendBlock({
  data,
  ctx,
}: {
  data: { days: TrendDay[]; hourly: HourlyCell[]; dow_band: { grid: ForecastOverviewGridCell[]; worst: ForecastOverviewWorst | null } }[];
  ctx: RangeCtx;
}) {
  const payload = data[0] ?? { days: [], hourly: [], dow_band: { grid: [], worst: null } };
  const rangeDays = Math.max(
    1,
    Math.round((new Date(ctx.to).getTime() - new Date(ctx.from).getTime()) / 86400000) + 1,
  );
  return (
    <div>
      <DowBandHeatmapCard grid={payload.dow_band.grid} worst={payload.dow_band.worst} rangeDays={rangeDays} />
      <DailyChart days={payload.days} />
      <HourlyHeatmap cells={payload.hourly} />
    </div>
  );
}

function DowBandHeatmapCard({
  grid,
  worst,
  rangeDays,
}: {
  grid: ForecastOverviewGridCell[];
  worst: ForecastOverviewWorst | null;
  rangeDays: number;
}) {
  const { t } = useTranslation();
  const dayLabel = (dow: number) => t(`forecast.dow_${WEEK[dow - 1]}`);
  const bandLabel = (b: Band) => t(`forecast.band_${b}`);
  const axisMin = t("forecast.axis_min");
  const values = grid.map((c) => c.expected_avg_min).filter((v): v is number => v != null);
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 0;

  return (
    <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border-soft)", borderRadius: "var(--radius)", padding: 16, marginBottom: 16 }}>
      <h3 style={{ marginTop: 0, fontSize: 14 }}>{t("reports.dow_band.title")}</h3>
      {values.length === 0 ? (
        <p style={{ color: "var(--text-tertiary)", fontSize: 13 }}>{t("reports.dow_band.empty")}</p>
      ) : (
        <>
          {worst && (
            <p style={{ color: "var(--text-secondary)", fontSize: 13 }}>
              {t("reports.dow_band.worst_phrase", {
                days: rangeDays,
                day: dayLabel(worst.dow),
                band: bandLabel(worst.band),
                min: worst.expected_avg_min.toFixed(1),
              })}
            </p>
          )}
          <BandGrid
            grid={grid}
            bandLabel={bandLabel}
            dayLabel={dayLabel}
            axisMin={axisMin}
            colorFor={delayColor}
            onTip={() => {}}
            onLeave={() => {}}
          />
          <Legend min={min} max={max} unit={axisMin} colorFor={delayColor} />
        </>
      )}
    </div>
  );
}
