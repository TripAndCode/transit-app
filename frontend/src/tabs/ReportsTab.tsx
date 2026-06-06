import { useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useReport, useReports } from "../api/hooks";
import { ctxToQueryString, useRangeContext } from "../api/rangeContext";
import type { TrendDay } from "../api/types";
import { TabFilterBar } from "../components/TabFilterBar";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { InsightHint } from "../components/InsightHint";
import { Skeleton } from "../components/Skeleton";
import { DailyChart } from "../components/charts/DailyChart";
import { HourlyHeatmap, type HourlyCell } from "../components/charts/HourlyHeatmap";
import { ReportTable } from "../components/ReportTable";

export function ReportsTab() {
  const { t } = useTranslation();
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

  const reportLabels: Record<string, string> = useMemo(
    () => ({
      ranking: t("reports.type.ranking"),
      ranking_best: t("reports.type.ranking_best"),
      on_time: t("reports.type.on_time"),
      worst_5min: t("reports.type.worst_5min"),
      trend: t("reports.type.trend"),
      compare_ranking: t("reports.type.compare_ranking"),
      dow_weekday: t("reports.type.dow_weekday"),
      dow_weekend: t("reports.type.dow_weekend"),
    }),
    [t],
  );

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
              onClick={() => navigate(`/agencies/${id}/reports/${r.report_type}${filterSuffix}`)}
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
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        {!reportType && (
          <EmptyState title={t("reports.select_prompt")} />
        )}
        {reportType && detail.error && (
          <ErrorBanner error={detail.error} onRetry={() => detail.refetch()} />
        )}
        {reportType && detail.isFetching && <Skeleton height={400} />}
        {detail.data && (
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
              <TrendBlock data={detail.data.rows as unknown as { days: TrendDay[]; hourly: HourlyCell[] }[]} />
            ) : detail.data.rows.length > 0 ? (
              <ReportTable
                reportType={detail.data.report_type}
                rows={detail.data.rows as unknown[][]}
              />
            ) : (
              <EmptyState title={t("reports.no_data.title")} hint={t("reports.no_data.hint")} />
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
