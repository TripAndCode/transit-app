import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { ApiError } from "../api/client";
import { JST_TZ, toJstISO } from "../api/scope";
import { useSnapshot, type Snapshot } from "../api/snapshots";
import type { ReportResponse } from "../api/types";
import { buildRouteNames, type RouteNames } from "../api/useRouteNames";
import { DefinitionMetaBlock, OnTimeDefinition } from "../components/DefinitionMetaBlock";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { LanguageToggle } from "../components/LanguageToggle";
import { ReportTable } from "../components/ReportTable";
import { SkeletonTable } from "../components/Skeleton";
import { CouncilSummaryBlock } from "../components/analysis/CouncilSummaryBlock";
import { RowsShown } from "../components/analysis/RankingCoverage";
import { reportLabel } from "../components/analysis/reportGroups";
import { contentLocale } from "../i18n/contentLocale";
import { useDocumentLocale } from "../i18n/useDocumentLocale";
import { routeTableSortDirection, type RouteTableView } from "../routes/destinations";
import { FILTER_SEPARATOR, formatDateRange, formatDuration } from "../utils/format";
import "./SnapshotPage.css";

const JST_CLOCK = new Intl.DateTimeFormat("en-GB", { timeZone: JST_TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

/** "YYYY-MM-DD HH:mm" on the JST clock, whatever the reader's own zone. */
function jstStamp(iso: string): string {
  const at = new Date(iso);
  return `${toJstISO(at)} ${JST_CLOCK.format(at)}`;
}

type RouteTableReport = Extract<ReportResponse, { report_type: "route_table" }>;
type CouncilReport = Extract<ReportResponse, { report_type: "council_summary" }>;

/** A frozen snapshot, which anyone holding its link may read: the figures a
 *  screen showed when an admin took it, drawn by the screen's own components
 *  from the stored responses alone. It sits outside the app shell and asks
 *  the API for nothing but the snapshot, so a reader with no account sees
 *  it, and nothing on it can move to today's figures. */
export function SnapshotPage() {
  const { snapshotId = "" } = useParams();
  const { t } = useTranslation();
  const snapshot = useSnapshot(snapshotId);
  useDocumentLocale([t("snapshot.page_title"), snapshot.data?.agency_name]);
  const missing = snapshot.error instanceof ApiError && snapshot.error.status === 404;
  return (
    <div className="snapshot-page">
      <header className="snapshot-page__header">
        <span>{t("header.app_title")}</span>
        <LanguageToggle />
      </header>
      <main className="snapshot-page__main">
        {snapshot.isPending && <SkeletonTable rows={8} rowHeight={36} />}
        {missing && <EmptyState title={t("snapshot.not_found.title")} hint={t("snapshot.not_found.hint")} />}
        {snapshot.error && !missing && <ErrorBanner error={snapshot.error} onRetry={() => void snapshot.refetch()} />}
        {snapshot.data && <SnapshotBody snapshot={snapshot.data} />}
      </main>
    </div>
  );
}

function SnapshotBody({ snapshot }: { snapshot: Snapshot }) {
  const { t, i18n } = useTranslation();
  const locale = contentLocale(i18n);
  const reports = snapshot.payloads.report ?? {};
  // Both languages are stored; one missing would still leave the figures.
  const report = reports[locale] ?? reports.ja ?? reports.en;
  const credit = snapshot.payloads.credit?.[locale] ?? null;
  const names = buildRouteNames(snapshot.payloads.routes ?? [], t, { agencyName: snapshot.agency_name });
  const scope = [snapshot.agency_name, report?.ctx && formatDateRange(report.ctx.from, report.ctx.to)].filter(Boolean);
  return (
    <>
      <div className="snapshot-banner" role="note">
        <p className="snapshot-banner__stamp">
          {t("snapshot.banner", { created: jstStamp(snapshot.created_at), method: snapshot.method_version })}
        </p>
        <p>{t("snapshot.frozen_note")}</p>
      </div>
      <h1 className="snapshot-page__title">{snapshot.title ?? screenTitle(snapshot.screen, t)}</h1>
      <p className="snapshot-page__scope">{scope.join(FILTER_SEPARATOR)}</p>
      {snapshot.note && <p className="snapshot-page__note">{snapshot.note}</p>}
      {report?.report_type === "route_table" ? (
        <RouteTableSnapshot report={report} names={names} />
      ) : report?.report_type === "council_summary" ? (
        <CouncilSnapshot report={report} />
      ) : (
        <EmptyState title={t("snapshot.unsupported")} />
      )}
      {credit && (
        <section className="snapshot-page__credit">
          <h2>{t("credits.title")}</h2>
          <p>{credit}</p>
        </section>
      )}
    </>
  );
}

function screenTitle(screen: string, t: TFunction): string {
  if (screen === "council_report") return reportLabel(t, "council_summary");
  if (screen === "route_table") return t("routeTable.title");
  return t("snapshot.page_title");
}

/** The Routes table as the live screen draws it, in the sort it was in,
 *  with no control to change what was stored. */
function RouteTableSnapshot({ report, names }: { report: RouteTableReport; names: RouteNames }) {
  const { t } = useTranslation();
  const view: RouteTableView = { sort: report.sort ?? "avg", order: report.order ?? "worst" };
  const early = report.definition.early_departure_sec;
  return (
    <section>
      <DefinitionMetaBlock definition={report.definition} />
      {report.rows.length > 0 ? (
        <>
          <ReportTable
            reportType="route_table"
            rows={report.rows}
            minSamples={report.reliable_min_samples}
            sortBy={view.sort}
            sortDirection={routeTableSortDirection(view)}
            earlyDepartureSec={early}
            names={names}
          />
          {report.rows_total != null && report.rows_total > report.rows.length && (
            <RowsShown shown={report.rows.length} total={report.rows_total} />
          )}
          <div className="snapshot-page__notes">
            <OnTimeDefinition definition={report.definition} />
            {early != null && <p>{t("routeTable.early_note", { early: formatDuration(early) })}</p>}
            <p>{t("routeTable.late5_note")}</p>
          </div>
        </>
      ) : (
        <EmptyState title={t("reports.no_data.title")} />
      )}
    </section>
  );
}

function CouncilSnapshot({ report }: { report: CouncilReport }) {
  const { t } = useTranslation();
  return (
    <section>
      <DefinitionMetaBlock definition={report.definition} />
      {report.rows.length > 0 ? <CouncilSummaryBlock report={report} /> : <EmptyState title={t("reports.no_data.title")} />}
    </section>
  );
}
