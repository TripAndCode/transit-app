import { use, useState } from "react";
import { useSearchParams } from "react-router-dom";
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
import { apiUrl } from "../api/client";
import { useJumpToLatestDataRange } from "../api/latestDataWindow";
import { DataEndContext, defaultPeriod, scopeToQueryString, useScope, type Scope } from "../api/scope";
import type { ReportResponse, TrendPayload } from "../api/types";
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
import { FILTER_SEPARATOR, formatDate, formatDateRange } from "../utils/format";
import { DefinitionMetaBlock } from "../components/DefinitionMetaBlock";
import { RouteForecastSection } from "../components/RouteForecastSection";
import { useRouteNames } from "../api/useRouteNames";
import { useAgencyId } from "../api/useAgencyId";
import { ReportList } from "../components/analysis/ReportList";
import { reportLabel } from "../components/analysis/reportGroups";
import { WhyAnalyses } from "../components/analysis/WhyAnalyses";
import "./analysisTab.css";
import { useIsAdmin } from "../api/useIsAdmin";
import { CouncilSummaryBlock } from "../components/analysis/CouncilSummaryBlock";
import { StillWorking } from "../components/StillWorking";
import { DelayCertificateLookup } from "../components/analysis/DelayCertificateLookup";
import { certificateDayScope } from "../components/analysis/certificateDayScope";
import { pickReport } from "../routes/destinations";
import { LeftOutToggle, RowsShown } from "../components/analysis/RankingCoverage";
import { CsvLink } from "../components/analysis/CsvLink";

// The route lists these screens host, which leave retired route codes and
// zero-delay routes out unless asked (api/routers/reports.py's
// _ROUTE_LIST_TYPES). The Routes screen's own table is RouteTableScreen.
const ROUTE_LIST_TYPES = new Set(["compare_ranking", "dow_weekday", "dow_weekend"]);

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

/** Which of the routes a route list leaves out by default it keeps. */
type KeptRoutes = Required<Pick<ReportOptions, "includeRetired" | "includeZeroDelay">>;

/** A route list's options: every route list can keep the routes it leaves
 *  out by default. The compare report is drawn re-ranked by one period's
 *  delay, so it always asks for the API's maximum length: the default length
 *  keeps only the widest gaps, which would drop a route that is slow in both
 *  periods. */
function rankingOptions(reportType: string | null | undefined, kept: KeptRoutes): ReportOptions | undefined {
  if (reportType == null || !ROUTE_LIST_TYPES.has(reportType)) return undefined;
  if (reportType === "compare_ranking") return { limit: REPORT_ROWS_MAX, ...kept };
  return { ...kept };
}

/** One screen's reports: the list shows only `reportTypes`, and the open
 *  report is the one `pickReport` resolves from the `report` param. */
export function AnalysisTab({
  reportTypes,
  defaultReport,
}: {
  reportTypes: readonly string[];
  defaultReport?: string | null;
}) {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const reportType = pickReport(searchParams.get("report"), reportTypes, defaultReport);
  const id = useAgencyId();
  const routeNames = useRouteNames(id);
  const [ctx, update] = useScope();
  const jumpToLatestData = useJumpToLatestDataRange(id);
  function selectReport(type: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("report", type);
      return next;
    });
  }
  function setFlag(param: string, on: boolean) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (on) next.set(param, "1");
      else next.delete(param);
      return next;
    });
  }
  const kept: KeptRoutes = {
    includeRetired: searchParams.get("retired") === "1",
    includeZeroDelay: searchParams.get("zero_delay") === "1",
  };
  const list = useReports(id);
  // The certificate, like route_forecast, owns its section and its queries.
  // Its day and route are its own scope, so the screen's period sentence,
  // which nothing on it reads, is left off.
  const isCertificate = reportType === "delay_certificate";
  const detail = useReport(
    id,
    reportType && reportType !== "route_forecast" && !isCertificate ? reportType : null,
    ctx,
    rankingOptions(reportType, kept),
  );
  const [rawRowsOpen, setRawRowsOpen] = useState(false);
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

  function csvHref(type: string): string {
    return apiUrl(`/api/${id}/reports/${type}?${reportQueryString(ctx, rankingOptions(type, kept))}&format=csv`);
  }

  // `route_forecast` is served by its own endpoint, so the reports list never
  // returns it -- it is appended here as list data rather than re-rendered as
  // a second, hand-copied button underneath the list.
  const listedTypes = [...(list.data ?? []).map((r) => r.report_type), "route_forecast"].filter((type) =>
    reportTypes.includes(type),
  );

  return (
    <div className="analysis-tab" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {!isCertificate && <ScopeSentence applied={scopeApplied} />}
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
        {isCertificate && id != null && (
          <CertificateSection key={id} aid={id} />
        )}
        {reportType && reportType !== "route_forecast" && !isCertificate && detail.error && (
          <ErrorBanner error={detail.error} onRetry={() => detail.refetch()} />
        )}
        {reportType && reportType !== "route_forecast" && !isCertificate && detail.isFetching && (
          <>
            <SkeletonChart height={360} />
            <StillWorking scope={ctx} update={update} />
          </>
        )}
        {reportType !== "route_forecast" && !isCertificate && detail.data && (
          <div>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
              <h2 style={{ margin: 0 }}>{reportLabel(t, detail.data.report_type)}</h2>
              {hasCsv(detail.data) && <CsvLink href={csvHref(detail.data.report_type)} />}
            </div>
            <ReportMeta data={detail.data} />
            {detail.data.retired_rows != null && detail.data.retired_rows > 0 && (
              <LeftOutToggle
                reason="retired"
                checked={kept.includeRetired}
                count={detail.data.retired_rows}
                onChange={(on) => setFlag("retired", on)}
              />
            )}
            {detail.data.zero_delay_rows != null && detail.data.zero_delay_rows > 0 && (
              <LeftOutToggle
                reason="zero_delay"
                checked={kept.includeZeroDelay}
                count={detail.data.zero_delay_rows}
                onChange={(on) => setFlag("zero_delay", on)}
              />
            )}
            {detail.data.report_type === "trend" ? (
              <TrendBlock data={detail.data.rows} ctx={ctx} />
            ) : detail.data.report_type === "dwell_run" ? (
              id != null && <WhyAnalyses aid={id} payload={detail.data.rows[0]} />
            ) : detail.data.report_type === "council_summary" && detail.data.rows.length > 0 ? (
              <CouncilSummaryBlock report={detail.data} />
            ) : detail.data.report_type === "compare_ranking" && detail.data.rows.length > 0 ? (
              <>
                <CompareBars rows={detail.data.rows} resetKey={`${id ?? "none"}:${scopeToQueryString(ctx)}`} />
                {/* Already the API's longest list, so there is no "show all". */}
                {detail.data.rows_total != null && detail.data.rows_total > detail.data.rows.length && (
                  <RowsShown shown={detail.data.rows.length} total={detail.data.rows_total} />
                )}
              </>
            ) : detail.data.rows.length > 0 ? (
              <ReportTable reportType={detail.data.report_type} rows={detail.data.rows} />
            ) : (
              <EmptyState
                title={t("reports.no_data.title")}
                hint={t("reports.no_data.hint")}
                reasons={buildFilterCtxReasons(ctx, t, routeNames.format)}
                recoveries={buildFilterCtxRecoveries({
                  ctx,
                  onClearRoutes: () => update({ routes: null }),
                  onResetService: () => update({ service: "all" }),
                  jumpToLatestData,
                  t,
                })}
              />
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

/** A passenger starts from the lookup, which fetches one day and one route.
 *  Staff can open every route's late departures for the same day, fetched
 *  only once they open the list, so it never holds the lookup back. The
 *  open state lives here, so leaving the certificate drops it and a return
 *  starts folded. */
function CertificateSection({ aid }: { aid: number }) {
  const { t } = useTranslation();
  const latestDay = defaultPeriod(use(DataEndContext)).to;
  const [pickedDate, setPickedDate] = useState<string | null>(null);
  const date = pickedDate ?? latestDay;
  const [staffOpen, setStaffOpen] = useState(false);
  const day = certificateDayScope(date, null);
  const all = useReport(aid, staffOpen ? "delay_certificate" : null, day);
  const data = all.data?.report_type === "delay_certificate" ? all.data : undefined;
  return (
    <div>
      <h2 style={{ margin: 0 }}>{reportLabel(t, "delay_certificate")}</h2>
      <DelayCertificateLookup aid={aid} date={date} latestDay={latestDay} onDateChange={setPickedDate} />
      <details className="cert-staff" onToggle={(e) => setStaffOpen(e.currentTarget.open)}>
        <summary>{t("reports.certificate.late_list")}</summary>
        {staffOpen && all.error && <ErrorBanner error={all.error} onRetry={() => all.refetch()} />}
        {staffOpen && all.isFetching && (
          <>
            {!data && <SkeletonTable rows={6} rowHeight={36} />}
            <StillWorking />
          </>
        )}
        {staffOpen && data && (
          <>
            <p className="cert-staff__summary">
              {t("reports.certificate.late_list_scope", { date: formatDate(date, { weekday: true }) })}
              {FILTER_SEPARATOR}
              {data.text}
            </p>
            <CsvLink href={apiUrl(`/api/${aid}/reports/delay_certificate?${reportQueryString(day)}&format=csv`)} />
            {data.rows.length > 0 && <ReportTable reportType={data.report_type} rows={data.rows} />}
          </>
        )}
      </details>
    </div>
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
  const routeNames = useRouteNames(useAgencyId());
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
            <DailyChart days={payload.days} revisionBoundaries={payload.revision_boundaries ?? []} routeName={routeNames.format} />
          </>
        )}
        <HourlyHeatmap cells={payload.hourly} />
      </div>
    </TrendFocusProvider>
  );
}
