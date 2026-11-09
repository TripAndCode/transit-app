import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { ApiError } from "../api/client";
import { JST_TZ, parseScope, toJstISO, type Scope } from "../api/scope";
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
import { LeftOutLine, RowsShown, SparseToggle } from "../components/analysis/RankingCoverage";
import { reportLabel } from "../components/analysis/reportGroups";
import { conditionIsSet, scopeTokens } from "../components/scope/scopePhrases";
import { contentLocale } from "../i18n/contentLocale";
import { useDocumentLocale } from "../i18n/useDocumentLocale";
import { routeTableSortDirection, type RouteTableView } from "../routes/destinations";
import { formatDuration } from "../utils/format";
import "./SnapshotPage.css";

const JST_CLOCK = new Intl.DateTimeFormat("en-GB", { timeZone: JST_TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

/** "YYYY-MM-DD HH:mm" on the JST clock, whatever the reader's own zone. */
function jstStamp(iso: string): string {
  const at = new Date(iso);
  return `${toJstISO(at)} ${JST_CLOCK.format(at)}`;
}

/** The scope the snapshot's figures were computed under, from its stored
 *  params, which name the dates it resolved to. */
function storedScope(params: Record<string, unknown>, report: ReportResponse | undefined): Scope {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value == null) continue;
    query.set(key, Array.isArray(value) ? value.join(",") : String(value));
  }
  const dates = report?.ctx ?? { from: String(params.from ?? ""), to: String(params.to ?? "") };
  return parseScope(query, { from: dates.from, to: dates.to });
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
  return (
    <>
      <div className="snapshot-banner" role="note">
        <p className="snapshot-banner__stamp">
          {t("snapshot.banner", { created: jstStamp(snapshot.created_at), method: snapshot.method_version })}
        </p>
        <p>{t("snapshot.frozen_note")}</p>
      </div>
      <h1 className="snapshot-page__title">{snapshot.title ?? screenTitle(snapshot.screen, t)}</h1>
      <ScopeStatement snapshot={snapshot} report={report} names={names} />
      {snapshot.note && <p className="snapshot-page__note">{snapshot.note}</p>}
      {report?.report_type === "route_table" ? (
        <RouteTableSnapshot report={report} names={names} params={snapshot.params} />
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

/** The live screen's scope sentence, read-only: every condition the figures
 *  were computed under, one the screen's data did not use struck through. */
function ScopeStatement({ snapshot, report, names }: { snapshot: Snapshot; report: ReportResponse | undefined; names: RouteNames }) {
  const { t } = useTranslation();
  const scope = storedScope(snapshot.params, report);
  const tokens = scopeTokens(scope, {
    t,
    agencyName: snapshot.agency_name,
    routeLabel: names.format,
    routeGroup: (code) => names.data.get(code),
  });
  const applied = report?.scope_applied;
  return (
    <p className="snapshot-page__scope" data-testid="snapshot-scope">
      {tokens.map((tok, i) => {
        const off = tok.field != null && applied?.[tok.field] === false && conditionIsSet(tok.key, scope);
        return (
          <span key={tok.key}>
            {i > 0 && t("common.list_separator")}
            {off ? <s title={t("scope.unapplied")}>{tok.label}</s> : tok.label}
          </span>
        );
      })}
    </p>
  );
}

function screenTitle(screen: string, t: TFunction): string {
  if (screen === "council_report") return reportLabel(t, "council_summary");
  if (screen === "route_table") return t("routeTable.title");
  return t("snapshot.page_title");
}

/** The Routes table as the live screen draws it, in the sort it was in,
 *  with no control to change what was stored. */
function RouteTableSnapshot({
  report,
  names,
  params,
}: {
  report: RouteTableReport;
  names: RouteNames;
  params: Record<string, unknown>;
}) {
  const { t } = useTranslation();
  const view: RouteTableView = { sort: report.sort ?? "avg", order: report.order ?? "worst" };
  const early = report.definition.early_departure_sec;
  const kept = { retired: params.include_retired === true, zero_delay: params.include_zero_delay === true };
  return (
    <section>
      <DefinitionMetaBlock definition={report.definition} />
      <div className="snapshot-page__coverage">
        {report.reliable_min_samples != null && (
          <SparseToggle checked={params.include_sparse === true} floor={report.reliable_min_samples} />
        )}
        <LeftOutLine counts={{ retired: report.retired_rows, zero_delay: report.zero_delay_rows }} kept={kept} />
      </div>
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
