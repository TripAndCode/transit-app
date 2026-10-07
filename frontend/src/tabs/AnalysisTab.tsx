import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  REPORT_ROWS_MAX,
  reportQueryString,
  useForecastHeatmap,
  useForecastOverview,
  useReport,
  useReports,
  type ReportOptions,
} from "../api/hooks";
import { useJumpToLatestDataRange } from "../api/latestDataWindow";
import { scopeToQueryString, useScope, type Scope } from "../api/scope";
import type { DwellRunPayload, ReportResponse, TrendPayload } from "../api/types";
import { ScopeSentence } from "../components/scope/ScopeSentence";
import { EmptyState } from "../components/EmptyState";
import { buildFilterCtxRecoveries, buildFilterCtxReasons } from "../components/emptyStateRecoveries";
import { ErrorBanner } from "../components/ErrorBanner";
import { InsightHint } from "../components/InsightHint";
import { InsightPanel } from "../components/InsightPanel";
import { SkeletonChart, SkeletonTable } from "../components/Skeleton";
import { DailyChart } from "../components/charts/DailyChart";
import { HourlyHeatmap } from "../components/charts/HourlyHeatmap";
import { HeatSurface } from "../components/charts/HeatSurface";
import { TrendFocusProvider } from "../components/charts/TrendFocusContext";
import { CompareBars } from "../components/charts/CompareBars";
import { ReportTable } from "../components/ReportTable";
import { HeadwayQualityPanel } from "../components/HeadwayQualityPanel";
import { PerformanceStandardPanel } from "../components/PerformanceStandardPanel";
import { WeatherDelayPanel } from "../components/WeatherDelayPanel";
import { formatNumber, formatDateRange } from "../utils/format";
import { serviceValueLabel } from "../utils/filterValueLabels";
import { DefinitionMetaBlock } from "../components/DefinitionMetaBlock";
import { RouteForecastSection } from "../components/RouteForecastSection";
import { useCappedList } from "../hooks/useCappedList";
import { useRouteNames } from "../api/useRouteNames";
import { RouteLabel } from "../components/RouteLabel";
import { useAgencyId } from "../api/useAgencyId";
import { SHARED_TABLE, th, td } from "../components/tableStyles";
import { ReportList } from "../components/analysis/ReportList";
import { reportLabel } from "../components/analysis/reportGroups";
import "./analysisTab.css";
import { useIsAdmin } from "../api/useIsAdmin";
import { ServiceNote } from "../components/ServiceNote";
import { CouncilSummaryBlock } from "../components/analysis/CouncilSummaryBlock";
import { StillWorking } from "../components/StillWorking";
import { DelayCertificateLookup } from "../components/analysis/DelayCertificateLookup";
import { destHref, reportHref } from "../routes/destinations";
import { RowsShown, SparseToggle } from "../components/analysis/RankingCoverage";

const RANKING_TYPES = new Set(["ranking", "ranking_best"]);

/** Whether a report has rows to export: the trend is a chart, and a dwell
 *  and running-time split this feed or filter can't make has none. */
function hasCsv(data: ReportResponse): boolean {
  if (data.report_type === "trend") return false;
  if (data.report_type === "dwell_run") {
    const payload = data.rows[0];
    return !!payload?.available && payload.time_band_supported !== false;
  }
  return true;
}

/** A ranking's coverage options; every other report takes none, and the API
 *  refuses them there. */
function rankingOptions(
  reportType: string | null | undefined,
  includeSparse: boolean,
  allRowsFor: string | null,
): ReportOptions | undefined {
  if (reportType == null || !RANKING_TYPES.has(reportType)) return undefined;
  return { includeSparse, limit: allRowsFor === reportType ? REPORT_ROWS_MAX : undefined };
}

/** One screen's reports: the list shows only `reportTypes`, and the open
 *  report is the `report` search param when it belongs to them, else
 *  `defaultReport` when it does, else the first one, so a stale link from
 *  another screen never opens a report this screen doesn't host. */
export function AnalysisTab({
  reportTypes,
  defaultReport,
}: {
  reportTypes: readonly string[];
  defaultReport?: string | null;
}) {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = searchParams.get("report");
  const fallback = defaultReport != null && reportTypes.includes(defaultReport) ? defaultReport : (reportTypes[0] ?? null);
  const reportType = requested != null && reportTypes.includes(requested) ? requested : fallback;
  const id = useAgencyId();
  const [ctx, update] = useScope();
  const jumpToLatestData = useJumpToLatestDataRange(id);
  function selectReport(type: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("report", type);
      return next;
    });
  }
  const includeSparse = searchParams.get("sparse") === "1";
  function setIncludeSparse(on: boolean) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (on) next.set("sparse", "1");
      else next.delete("sparse");
      return next;
    });
  }
  // "Show all" belongs to the ranking it was asked on; another report opens
  // at the default length.
  const [allRowsFor, setAllRowsFor] = useState<string | null>(null);
  const list = useReports(id);
  const detail = useReport(
    id,
    reportType && reportType !== "route_forecast" ? reportType : null,
    ctx,
    rankingOptions(reportType, includeSparse, allRowsFor),
  );
  const [rawRowsOpen, setRawRowsOpen] = useState(false);
  const [staffOpen, setStaffOpen] = useState(false);
  const isAdmin = useIsAdmin();
  // route_forecast is served by the forecast endpoints, so their own map
  // applies: one chosen route opens that route's forecast, which honours the
  // route; otherwise the agency-wide overview. A report's map counts only
  // once that report's response is the one on screen, not the previous
  // report kept as placeholder data.
  const forecastRoute = reportType === "route_forecast" && ctx.routes.length === 1 ? ctx.routes[0] : null;
  const forecast = useForecastOverview(reportType === "route_forecast" ? id : null);
  const routeForecast = useForecastHeatmap(forecastRoute != null ? id : null, forecastRoute ?? "");
  const scopeApplied =
    reportType === "route_forecast"
      ? forecastRoute != null
        ? routeForecast.data?.scope_applied
        : forecast.data?.scope_applied
      : detail.data?.report_type === reportType
        ? detail.data.scope_applied
        : undefined;

  const isCertificate = detail.data?.report_type === "delay_certificate";
  function csvHref(type: string): string {
    return `/api/${id}/reports/${type}?${reportQueryString(ctx, rankingOptions(type, includeSparse, allRowsFor))}&format=csv`;
  }

  // `route_forecast` is served by its own endpoint, so the reports list never
  // returns it -- it is appended here as list data rather than re-rendered as
  // a second, hand-copied button underneath the list.
  const listedTypes = [...(list.data ?? []).map((r) => r.report_type), "route_forecast"].filter((type) =>
    reportTypes.includes(type),
  );

  return (
    <div className="analysis-tab" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <ScopeSentence applied={scopeApplied} />
      <div className="analysis-body" style={{ display: "flex", gap: 16, flex: 1, minHeight: 0 }}>
      <div className="analysis-report-list" style={{ width: 280, flexShrink: 0 }}>
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
        {list.isLoading && <SkeletonTable rows={6} rowHeight={48} />}
        {list.data && list.data.length === 0 && (
          <EmptyState
            title={t("reports.empty.title")}
            hint={t("reports.empty.hint")}
          />
        )}
        {list.data && (
          <ReportList
            types={listedTypes}
            active={reportType ?? null}
            onSelect={selectReport}
          />
        )}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        {!reportType && (
          <EmptyState title={t("reports.select_prompt")} />
        )}
        {reportType === "route_forecast" && id != null && (
          <div>
            <h2 style={{ margin: "0 0 16px" }}>{reportLabel(t, "route_forecast")}</h2>
            <RouteForecastSection aid={id} />
          </div>
        )}
        {reportType && reportType !== "route_forecast" && detail.error && (
          <ErrorBanner error={detail.error} onRetry={() => detail.refetch()} />
        )}
        {reportType && reportType !== "route_forecast" && detail.isFetching && (
          <>
            <SkeletonChart height={360} />
            <StillWorking scope={ctx} update={update} />
          </>
        )}
        {reportType !== "route_forecast" && detail.data && (
          <div>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
              <h2 style={{ margin: 0 }}>{reportLabel(t, detail.data.report_type)}</h2>
              {hasCsv(detail.data) && !isCertificate && (
                <CsvLink href={csvHref(detail.data.report_type)} />
              )}
            </div>
            {!isCertificate && <ReportMeta data={detail.data} />}
            {detail.data.reliable_min_samples != null && (
              <SparseToggle checked={includeSparse} floor={detail.data.reliable_min_samples} onChange={setIncludeSparse} />
            )}
            {detail.data.report_type === "trend" ? (
              <TrendBlock data={detail.data.rows} ctx={ctx} />
            ) : detail.data.report_type === "dwell_run" ? (
              <DwellRunBlock payload={detail.data.rows[0]} />
            ) : detail.data.report_type === "council_summary" && detail.data.rows.length > 0 ? (
              <CouncilSummaryBlock row={detail.data.rows[0]} text={detail.data.text} />
            ) : detail.data.report_type === "delay_certificate" && id != null ? (
              <>
                <DelayCertificateLookup aid={id} />
                {/* The period's whole list, under the page's scope and the
                    report's own threshold, is for staff; a passenger starts
                    from the lookup above. */}
                <details className="cert-staff" onToggle={(e) => setStaffOpen(e.currentTarget.open)}>
                  <summary>{t("reports.certificate.all_late")}</summary>
                  {staffOpen && (
                    <>
                      <ReportMeta data={detail.data} />
                      <p className="cert-staff__summary">{detail.data.text}</p>
                      <CsvLink href={csvHref(detail.data.report_type)} />
                      {detail.data.rows.length > 0 && <ReportTable reportType={detail.data.report_type} rows={detail.data.rows} />}
                    </>
                  )}
                </details>
              </>
            ) : detail.data.report_type === "compare_ranking" && detail.data.rows.length > 0 ? (
              <CompareBars rows={detail.data.rows} />
            ) : detail.data.rows.length > 0 ? (
              <>
                <ReportTable
                  reportType={detail.data.report_type}
                  rows={detail.data.rows}
                  minSamples={detail.data.reliable_min_samples}
                />
                {detail.data.rows_total != null && detail.data.rows_total > detail.data.rows.length && (
                  <RowsShown
                    shown={detail.data.rows.length}
                    total={detail.data.rows_total}
                    onShowAll={allRowsFor === reportType ? undefined : () => setAllRowsFor(reportType)}
                  />
                )}
              </>
            ) : (
              <EmptyState
                title={t("reports.no_data.title")}
                hint={t("reports.no_data.hint")}
                reasons={buildFilterCtxReasons(ctx, t)}
                recoveries={buildFilterCtxRecoveries({
                  ctx,
                  onClearRoutes: () => update({ routes: null }),
                  onResetService: () => update({ service: "all" }),
                  jumpToLatestData,
                  t,
                })}
              />
            )}
            {/* Evidence panels, rendered alongside (never instead of) the
                report above: dwell vs run pairs with rain and long gaps;
                on-time pairs with headway quality and the agency's
                performance targets. The targets panel renders nothing
                when no standards are configured; the rain panel says so when
                no weather station is mapped. */}
            {detail.data.report_type === "dwell_run" && id != null && (
              <>
                <WeatherDelayPanel aid={id} ctx={ctx} />
                <HeadwayQualityPanel aid={id} ctx={ctx} />
              </>
            )}
            {detail.data.report_type === "on_time" && id != null && (
              <>
                <HeadwayQualityPanel aid={id} ctx={ctx} />
                <PerformanceStandardPanel aid={id} ctx={ctx} />
              </>
            )}
            {/* The API's raw rows help someone checking the pipeline, not
                someone reading the report. */}
            {isAdmin && detail.data.report_type !== "trend" && detail.data.rows.length > 0 && (
              <details
                style={{ marginTop: 16, color: "var(--text-tertiary)" }}
                onToggle={(e) => setRawRowsOpen(e.currentTarget.open)}
              >
                <summary style={{ cursor: "pointer", fontSize: 12 }}>
                  {t("reports.raw_rows", { count: detail.data.rows.length })}
                </summary>
                {rawRowsOpen && (
                  <pre
                    style={{
                      background: "var(--bg-surface)",
                      border: "1px solid var(--border-soft)",
                      borderRadius: "var(--radius)",
                      padding: 12,
                      marginTop: 8,
                      whiteSpace: "pre-wrap",
                      fontFamily: "var(--font-mono)",
                      fontSize: 12,
                      lineHeight: 1.6,
                      maxWidth: 920,
                    }}
                  >
                    {detail.data.text}
                  </pre>
                )}
              </details>
            )}
          </div>
        )}
      </div>
      {/* key={id} forces a remount on agency switch -- InsightPanel's `seen`
          state is seeded once from sessionStorage per mount; without this,
          switching agencies would keep the previous agency's exclude set
          in React state even though its sessionStorage key is now separate. */}
      <InsightPanel key={id} className="analysis-insights" />
      </div>
    </div>
  );
}

function CsvLink({ href }: { href: string }) {
  const { t } = useTranslation();
  return (
    <a
      href={href}
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
      <span aria-hidden="true">⬇ </span>
      {t("reports.download_csv")}
    </a>
  );
}

/** The period a report covers and the definitions it was computed under. */
function ReportMeta({ data }: { data: ReportResponse }) {
  const { t } = useTranslation();
  return (
    <>
      {data.ctx && (
        <div style={{ color: "var(--text-tertiary)", fontSize: 13, margin: "8px 0 4px" }}>
          {t("reports.range_suffix", { range: formatDateRange(data.ctx.from, data.ctx.to) })}
        </div>
      )}
      {data.definition && <DefinitionMetaBlock definition={data.definition} />}
    </>
  );
}

function TrendBlock({
  data,
  ctx,
}: {
  data: TrendPayload[];
  ctx: Scope;
}) {
  const { t } = useTranslation();
  const payload: TrendPayload = data[0] ?? {
    days: [],
    hourly: [],
    dow_band: { grid: [], worst: null },
    revision_boundaries: [],
  };
  const rangeDays = Math.max(
    1,
    Math.round((new Date(ctx.to).getTime() - new Date(ctx.from).getTime()) / 86400000) + 1,
  );
  // One provider over all three charts: hovering a mark in any of them dims
  // the marks in the others that don't share its day or weekday.
  return (
    <TrendFocusProvider>
      <div>
        {/* Over one day the weekday bands and the daily line each hold a
            single point; the hourly heatmap still reads. */}
        {rangeDays < 2 ? (
          <p className="trend-single-day">{t("reports.trend.single_day")}</p>
        ) : (
          <>
            <HeatSurface hourly={payload.hourly} grid={payload.dow_band.grid} worst={payload.dow_band.worst} rangeDays={rangeDays} />
            <DailyChart days={payload.days} revisionBoundaries={payload.revision_boundaries ?? []} />
          </>
        )}
        <HourlyHeatmap cells={payload.hourly} />
      </div>
    </TrendFocusProvider>
  );
}

function DwellRunBlock({ payload }: { payload: DwellRunPayload | undefined }) {
  const { t } = useTranslation();
  const id = useAgencyId();
  const routeNames = useRouteNames(id);
  const [ctx, update] = useScope();
  // The agency and filters the report was fetched for identify the list: a
  // refetch under the same ones is the same list, however new its objects are.
  const cappedRoutes = useCappedList(payload?.routes ?? [], 200, `${id ?? "none"}:${scopeToQueryString(ctx)}`);
  const jumpToLatestData = useJumpToLatestDataRange(id);
  const navigate = useNavigate();

  if (!payload || !payload.available) {
    const search = `?${scopeToQueryString(ctx)}`;
    return (
      <EmptyState
        title={t("reports.dwell_run.needs_arrivals")}
        hint={t("reports.dwell_run.not_available")}
        recoveries={
          id == null
            ? []
            : [
                { label: t("reports.dwell_run.see_when"), onClick: () => navigate(destHref(id, "time", search)) },
                { label: t("reports.dwell_run.compare_day_types"), onClick: () => navigate(reportHref(id, "compare_ranking", search)) },
              ]
        }
      />
    );
  }
  if (!payload.time_band_supported) {
    return <EmptyState title={t("reports.dwell_run.time_band_unsupported")} />;
  }
  if (payload.routes.length === 0) {
    return (
      <EmptyState
        title={t("reports.no_data.title")}
        hint={t("reports.no_data.hint")}
        reasons={buildFilterCtxReasons(ctx, t)}
        recoveries={buildFilterCtxRecoveries({
          ctx,
          onClearRoutes: () => update({ routes: null }),
          onResetService: () => update({ service: "all" }),
          jumpToLatestData,
          t,
        })}
      />
    );
  }

  const fmtSec = (v: number | null): string => (v == null ? "—" : `${v.toFixed(0)}${t("common.unit_sec")}`);
  const fmtSamples = (v: number): string => formatNumber(v);

  return (
    <div style={{ width: "100%", overflowX: "auto" }}>
      <table style={SHARED_TABLE}>
        <thead>
          <tr style={{ background: "var(--bg-soft)" }}>
            <th style={th({ width: 40 })}>#</th>
            <th style={th()}>{t("common.route")}</th>
            <th style={th()}>{t("reports.col.service")}</th>
            <th style={{ ...th(), textAlign: "right" }}>{t("reports.dwell_run.col.dwell_avg")}</th>
            <th style={{ ...th(), textAlign: "right" }}>{t("reports.dwell_run.col.dwell_p50")}</th>
            <th style={{ ...th(), textAlign: "right" }}>{t("reports.dwell_run.col.dwell_p90")}</th>
            <th style={{ ...th(), textAlign: "right" }}>{t("reports.dwell_run.col.dwell_samples")}</th>
            <th style={{ ...th(), textAlign: "right" }}>{t("reports.dwell_run.col.run_avg")}</th>
            <th style={{ ...th(), textAlign: "right" }}>{t("reports.dwell_run.col.run_p50")}</th>
            <th style={{ ...th(), textAlign: "right" }}>{t("reports.dwell_run.col.run_p90")}</th>
            <th style={{ ...th(), textAlign: "right" }}>{t("reports.dwell_run.col.run_samples")}</th>
          </tr>
        </thead>
        <tbody>
          {cappedRoutes.visible.map((r, i) => (
            <tr key={`${r.route_code}-${r.service_type ?? ""}`} style={{ borderTop: "1px solid var(--border-soft)" }}>
              <td style={{ ...td({ align: "right" }), color: "var(--text-tertiary)" }}>{i + 1}</td>
              <td style={{ ...td(), fontWeight: 500 }}><RouteLabel code={r.route_code} names={routeNames} /></td>
              <td style={td()}>{r.service_type ? serviceValueLabel(r.service_type, t) : "—"}</td>
              <td style={td({ align: "right" })}>{fmtSec(r.dwell_avg_sec)}</td>
              <td style={td({ align: "right" })}>{fmtSec(r.dwell_p50_sec)}</td>
              <td style={td({ align: "right" })}>{fmtSec(r.dwell_p90_sec)}</td>
              <td style={td({ align: "right" })}>{fmtSamples(r.dwell_samples)}</td>
              <td style={td({ align: "right" })}>{fmtSec(r.run_avg_sec)}</td>
              <td style={td({ align: "right" })}>{fmtSec(r.run_p50_sec)}</td>
              <td style={td({ align: "right" })}>{fmtSec(r.run_p90_sec)}</td>
              <td style={td({ align: "right" })}>{fmtSamples(r.run_samples)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <ServiceNote />
      {cappedRoutes.remaining > 0 && (
        <button type="button" className="btn-ghost" onClick={cappedRoutes.showMore}>
          {t("common.show_more", { count: cappedRoutes.remaining })}
        </button>
      )}
    </div>
  );
}
