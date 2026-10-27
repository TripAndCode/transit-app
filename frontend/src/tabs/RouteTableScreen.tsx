import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { REPORT_ROWS_MAX, reportQueryString, useReport, type ReportOptions } from "../api/hooks";
import { apiUrl } from "../api/client";
import { useJumpToLatestDataRange } from "../api/latestDataWindow";
import { useScope } from "../api/scope";
import { useAgencyId } from "../api/useAgencyId";
import { useRouteNames } from "../api/useRouteNames";
import { ScopeSentence } from "../components/scope/ScopeSentence";
import { EmptyState } from "../components/EmptyState";
import { buildFilterCtxRecoveries, buildFilterCtxReasons } from "../components/emptyStateRecoveries";
import { ErrorBanner } from "../components/ErrorBanner";
import { InsightPanel } from "../components/InsightPanel";
import { SkeletonTable } from "../components/Skeleton";
import { StillWorking } from "../components/StillWorking";
import { ReportTable } from "../components/ReportTable";
import { DefinitionMetaBlock, OnTimeDefinition } from "../components/DefinitionMetaBlock";
import { HeadwayQualityPanel } from "../components/HeadwayQualityPanel";
import { PerformanceStandardPanel } from "../components/PerformanceStandardPanel";
import { CsvLink } from "../components/analysis/CsvLink";
import { LeftOutLine, RowsShown, SparseToggle } from "../components/analysis/RankingCoverage";
import {
  ROUTE_TABLE_SORTS,
  ROUTE_TABLE_SORT_LABELS,
  routeTableView,
  type RouteTableOrder,
  type RouteTableView,
} from "../routes/destinations";
import { formatDateRange } from "../utils/format";
import "./analysisTab.css";
import "./routeTable.css";

/** How many routes the table lists before "show all". */
const TABLE_ROWS = 50;

const ORDERS = [
  ["worst", "routeTable.order.worst"],
  ["best", "routeTable.order.best"],
] as const satisfies readonly (readonly [RouteTableOrder, string])[];

/** Which way the sorted figure runs down the table. A high on-time rate is
 *  good and every other figure's high end needs attention, so the two read
 *  opposite ways for the same order. */
function sortDirection({ sort, order }: RouteTableView): "ascending" | "descending" {
  return (sort !== "on_time") === (order === "worst") ? "descending" : "ascending";
}

/** The days a window covers, both ends included. */
function windowDays(from: string, to: string): number {
  return Math.round((Date.parse(to) - Date.parse(from)) / 86_400_000) + 1;
}

/** The Routes screen: every route in one table, sorted by the figure the
 *  reader picks, each row opening its route. The sort and order live in the
 *  URL (`sort`, `order`), and a link naming one of the route lists the table
 *  replaced opens it sorted the way that list was (`routeTableView`). */
export function RouteTableScreen() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const id = useAgencyId();
  const routeNames = useRouteNames(id);
  const [ctx, update] = useScope();
  const jumpToLatestData = useJumpToLatestDataRange(id);
  const view = routeTableView(searchParams);
  const includeSparse = searchParams.get("sparse") === "1";
  const kept = { retired: searchParams.get("retired") === "1", zero_delay: searchParams.get("zero_delay") === "1" };
  // "Show all" lasts while the agency does; a new sort keeps the length.
  const [showAll, setShowAll] = useState(false);
  const options: ReportOptions = {
    sort: view.sort,
    order: view.order === "best" ? "best" : undefined,
    includeSparse,
    includeRetired: kept.retired,
    includeZeroDelay: kept.zero_delay,
    limit: showAll ? REPORT_ROWS_MAX : TABLE_ROWS,
  };
  const report = useReport(id, "route_table", ctx, options);
  const data = report.data?.report_type === "route_table" ? report.data : undefined;
  // The table's sort marks follow the rows on screen: while another sort
  // loads, the rows held are still in the order they arrived in.
  const shown: RouteTableView = data?.sort ? { sort: data.sort, order: data.order ?? "worst" } : view;

  /** Writes the table's params. The current sort and order go into `sort`
   *  and `order` first, in place of a former list's `report`, so changing one
   *  thing keeps the rest of what the reader sees. */
  function setTableParams(patch: Record<string, string | null>) {
    setSearchParams((prev) => {
      const current = routeTableView(prev);
      const next = new URLSearchParams(prev);
      next.delete("report");
      next.set("sort", current.sort);
      if (current.order === "best") next.set("order", "best");
      else next.delete("order");
      for (const [key, value] of Object.entries(patch)) {
        if (value == null) next.delete(key);
        else next.set(key, value);
      }
      return next;
    });
  }

  return (
    <div className="analysis-tab route-table-screen">
      <ScopeSentence applied={data?.scope_applied} />
      <div className="analysis-body route-table-screen__body">
        <div className="route-table-screen__main">
          <div className="route-table__head">
            <h2>{t("routeTable.title")}</h2>
            {id != null && data && (
              <CsvLink href={apiUrl(`/api/${id}/reports/route_table?${reportQueryString(ctx, options)}&format=csv`)} />
            )}
          </div>
          {data?.ctx && (
            <p className="route-table__window">
              {t("routeTable.window", {
                count: windowDays(data.ctx.from, data.ctx.to),
                range: formatDateRange(data.ctx.from, data.ctx.to, { year: false }),
              })}
            </p>
          )}
          {data?.definition && <DefinitionMetaBlock definition={data.definition} />}
          <div className="route-table__controls">
            <div className="route-table__seg" role="group" aria-label={t("routeTable.sort.label")}>
              {ROUTE_TABLE_SORTS.map((sort) => (
                <button key={sort} type="button" aria-pressed={view.sort === sort} onClick={() => setTableParams({ sort })}>
                  {t(ROUTE_TABLE_SORT_LABELS[sort])}
                </button>
              ))}
            </div>
            <div className="route-table__seg" role="group" aria-label={t("routeTable.order.label")}>
              {ORDERS.map(([order, labelKey]) => (
                <button
                  key={order}
                  type="button"
                  aria-pressed={view.order === order}
                  onClick={() => setTableParams({ order: order === "best" ? "best" : null })}
                >
                  {t(labelKey)}
                </button>
              ))}
            </div>
          </div>
          {data && (
            <div className="route-table__coverage">
              {data.reliable_min_samples != null && (
                <SparseToggle
                  checked={includeSparse}
                  floor={data.reliable_min_samples}
                  onChange={(on) => setTableParams({ sparse: on ? "1" : null })}
                />
              )}
              <LeftOutLine
                counts={{ retired: data.retired_rows, zero_delay: data.zero_delay_rows }}
                kept={kept}
                onChange={(reason, on) => setTableParams({ [reason]: on ? "1" : null })}
              />
            </div>
          )}
          {report.error && <ErrorBanner error={report.error} onRetry={() => report.refetch()} />}
          {report.isFetching && (
            <>
              {!data && <SkeletonTable rows={10} rowHeight={40} />}
              <StillWorking scope={ctx} update={update} />
            </>
          )}
          {data &&
            (data.rows.length > 0 ? (
              <div className="route-table__body" aria-busy={report.isFetching || undefined}>
                <ReportTable
                  reportType="route_table"
                  rows={data.rows}
                  minSamples={data.reliable_min_samples}
                  sortBy={shown.sort}
                  sortDirection={sortDirection(shown)}
                />
                {data.rows_total != null && data.rows_total > data.rows.length && (
                  <RowsShown
                    shown={data.rows.length}
                    total={data.rows_total}
                    onShowAll={showAll ? undefined : () => setShowAll(true)}
                  />
                )}
                <div className="route-table__notes">
                  <OnTimeDefinition definition={data.definition} />
                  <p>{t("routeTable.late5_note")}</p>
                </div>
              </div>
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
            ))}
          {/* Punctuality's evidence beside the on-time sort: headway quality
              and the agency's performance targets. The targets panel renders
              nothing when no standards are configured. */}
          {view.sort === "on_time" && id != null && (
            <>
              <HeadwayQualityPanel aid={id} ctx={ctx} />
              <PerformanceStandardPanel aid={id} ctx={ctx} />
            </>
          )}
        </div>
        {/* key={id}: InsightPanel seeds its `seen` set from sessionStorage
            once per mount, and that set belongs to one agency. */}
        <InsightPanel key={id} className="analysis-insights" />
      </div>
    </div>
  );
}
