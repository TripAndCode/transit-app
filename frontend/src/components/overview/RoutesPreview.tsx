import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useReport, type ReportOptions } from "../../api/hooks";
import { scopeToQueryString, type Scope } from "../../api/scope";
import { reportHref } from "../../routes/destinations";
import { onTimeReferenceMark } from "../analysis/routeReferenceText";
import { ErrorBanner } from "../ErrorBanner";
import { ReportTable } from "../ReportTable";
import { SkeletonTable } from "../Skeleton";
import { MetricDefinition } from "./MetricDefinition";
import { windowDays } from "./storySentence";

/** The first rows of the Routes table at its default sort and order, so the
 *  preview and the table it opens list the same routes with the same figures. */
const PREVIEW_ROWS = 5;
const PREVIEW_OPTIONS: ReportOptions = { sort: "avg", limit: PREVIEW_ROWS };

/** Where the preview's link leads: the Routes table over the same scope,
 *  sorted as the preview is. */
function routesPreviewHref(agencyId: number, scope: Scope): string {
  return reportHref(agencyId, "ranking", scopeToQueryString(scope));
}

/** Pulse's routes to check: the Routes table's own rows for `scope`, cut to
 *  the first few, and a link to the whole table. */
export function RoutesPreview({ agencyId, scope, range }: { agencyId: number; scope: Scope; range: string }) {
  const { t } = useTranslation();
  const report = useReport(agencyId, "route_table", scope, PREVIEW_OPTIONS);
  const data = report.data?.report_type === "route_table" ? report.data : undefined;
  const total = data?.rows_total ?? data?.rows.length ?? null;
  const reference = data?.reference ?? null;
  return (
    <div>
      <p className="ov-check-section-hd">
        {t("overview.routes_to_check.title", { count: windowDays(scope.from, scope.to), range })}{" "}
        <MetricDefinition
          title={t("overview.definition.routes_to_check.title")}
          body={t("overview.definition.routes_to_check.body", { floor: data?.reliable_min_samples ?? "—" })}
          manual="pulse"
        />
      </p>
      {data && total != null && total > 0 && data.reliable_min_samples != null && (
        <p className="ov-check-scope">
          {t("overview.routes_to_check.scope", { count: total, min: data.reliable_min_samples })}
        </p>
      )}
      {report.error ? (
        <ErrorBanner error={report.error} onRetry={() => void report.refetch()} />
      ) : !data ? (
        <SkeletonTable rows={PREVIEW_ROWS} />
      ) : data.rows.length === 0 ? (
        <p className="ov-check-empty">{t("overview.routes_to_check.empty")}</p>
      ) : (
        <ReportTable
          reportType="route_table"
          rows={data.rows}
          minSamples={data.reliable_min_samples}
          sortBy="avg"
          sortDirection="descending"
          earlyDepartureSec={data.definition.early_departure_sec}
          cellMarks={reference ? { 2: (row) => onTimeReferenceMark(reference, row, t) } : undefined}
          showStatistics={false}
        />
      )}
      <Link className="ov-check-more" to={routesPreviewHref(agencyId, scope)}>
        {total != null && total > 0
          ? t("overview.routes_to_check.see_all", { count: total })
          : t("overview.routes_to_check.open_table")}
      </Link>
    </div>
  );
}
