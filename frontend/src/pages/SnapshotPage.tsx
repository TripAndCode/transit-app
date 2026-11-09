import { useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { ApiError } from "../api/client";
import { JST_TZ, parseScope, toJstISO, type Scope } from "../api/scope";
import { useSnapshot, useSnapshotToday, type Snapshot, type SnapshotToday } from "../api/snapshots";
import type { DefinitionMeta, ReportResponse } from "../api/types";
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
import { isScopeConditionOff, scopeTokens } from "../components/scope/scopePhrases";
import {
  changedCouncilTiles,
  compareRouteTables,
  differenceCount,
  sameOnTimeWindow,
  type CouncilTile,
  type RowMark,
} from "../components/snapshotCompare";
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

/** The stored report against today's for the same query: the Routes table's
 *  rows with their marks, or the council report's moved tiles, and how many
 *  differences either holds. */
type Comparison =
  | { report_type: "route_table"; today: RouteTableReport; rows: RouteTableReport["rows"]; marks: RowMark[]; count: number }
  | { report_type: "council_summary"; today: CouncilReport; changed: Set<CouncilTile>; count: number };

function compareWithToday(report: ReportResponse | undefined, today: ReportResponse | null | undefined): Comparison | null {
  if (report?.report_type === "route_table" && today?.report_type === "route_table") {
    const { rows, marks } = compareRouteTables(report.rows, today.rows);
    return { report_type: "route_table", today, rows, marks, count: differenceCount(marks) };
  }
  if (report?.report_type === "council_summary" && today?.report_type === "council_summary") {
    const changed = changedCouncilTiles(report, today);
    return { report_type: "council_summary", today, changed, count: changed.size };
  }
  return null;
}

/** A frozen snapshot, which anyone holding its link may read: the figures a
 *  screen showed when an admin took it, drawn by the screen's own components
 *  from the stored responses alone. It sits outside the app shell, so a
 *  reader with no account sees it. It asks the API for the snapshot, and for
 *  today's figures only once the reader turns the comparison on; the stored
 *  figures stay as they were, today's shown beside them. */
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
  const comparable = report?.report_type === "route_table" || report?.report_type === "council_summary";
  const [comparing, setComparing] = useState(false);
  const today = useSnapshotToday(snapshot.snapshot_id, comparing && comparable);
  const comparison = comparing ? compareWithToday(report, today.data?.report) : null;
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
      {comparable && (
        <section className="snapshot-compare">
          <label className="snapshot-compare__toggle">
            <input type="checkbox" checked={comparing} onChange={(e) => setComparing(e.target.checked)} />
            {t("snapshot.compare.toggle")}
          </label>
          {comparing && report && (
            <TodayStatus
              report={report}
              today={today.data}
              loading={today.isLoading}
              error={today.error}
              onRetry={() => void today.refetch()}
              comparison={comparison}
            />
          )}
        </section>
      )}
      {report?.report_type === "route_table" ? (
        <RouteTableSnapshot
          report={report}
          names={names}
          params={snapshot.params}
          comparison={comparison?.report_type === "route_table" ? comparison : undefined}
        />
      ) : report?.report_type === "council_summary" ? (
        <CouncilSnapshot report={report} comparison={comparison?.report_type === "council_summary" ? comparison : undefined} />
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
  return (
    <p className="snapshot-page__scope" data-testid="snapshot-scope">
      {tokens.map((tok, i) => {
        const off = isScopeConditionOff(tok, scope, report?.scope_applied);
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

/** What the comparison with today rests on: each side's method, build and
 *  on-time definition, a plain word where the method or the on-time window
 *  differs, and how many figures moved; or why there are no figures today. */
function TodayStatus({
  report,
  today,
  loading,
  error,
  onRetry,
  comparison,
}: {
  report: ReportResponse;
  today: SnapshotToday | undefined;
  loading: boolean;
  error: Error | null;
  onRetry: () => void;
  comparison: Comparison | null;
}) {
  const { t } = useTranslation();
  if (loading) return <p className="snapshot-compare__note">{t("snapshot.compare.loading")}</p>;
  if (error) return <ErrorBanner error={error} onRetry={onRetry} prefix={t("snapshot.compare.unavailable_error")} />;
  if (today?.unavailable) {
    return (
      <p className="snapshot-compare__note" role="status">
        {t(`snapshot.compare.unavailable_${today.unavailable}`)}
      </p>
    );
  }
  if (comparison == null) return null;
  const before = report.definition;
  const after = comparison.today.definition;
  const oneSided = comparison.report_type === "route_table" && comparison.marks.some((m) => m.side !== "both");
  return (
    <div className="snapshot-compare__status">
      <p>{t("snapshot.compare.explainer")}</p>
      <dl className="snapshot-compare__sides" data-testid="snapshot-compare-sides">
        <Side label={t("snapshot.compare.side_snapshot")} definition={before} />
        <Side label={t("snapshot.compare.side_today")} definition={after} />
      </dl>
      {before.method_version !== after.method_version && (
        <p>{t("snapshot.compare.method_differs", { today: after.method_version, snapshot: before.method_version })}</p>
      )}
      {!sameOnTimeWindow(before, after) && <p>{t("snapshot.compare.on_time_differs")}</p>}
      <p className="snapshot-compare__count">
        {comparison.count > 0 ? t("snapshot.compare.differences", { count: comparison.count }) : t("snapshot.compare.no_differences")}
      </p>
      {oneSided && <p>{t("snapshot.compare.one_side_note")}</p>}
    </div>
  );
}

function Side({ label, definition }: { label: string; definition: DefinitionMeta }) {
  const { t } = useTranslation();
  return (
    <>
      <dt>{label}</dt>
      <dd>
        <p>{t("snapshot.compare.stamp", { method: definition.method_version, built: builtWords(definition, t) })}</p>
        <OnTimeDefinition definition={definition} />
      </dd>
    </>
  );
}

/** When a side's aggregates were built, on the JST clock, or that it was a
 *  live scan, which has no build. */
function builtWords(definition: DefinitionMeta, t: TFunction): string {
  if (definition.source === "live") return t("snapshot.compare.built_live");
  const at = definition.built_at;
  if (at == null || isNaN(new Date(at).getTime())) return t("snapshot.compare.built_unknown");
  return t("snapshot.compare.built_at", { at: jstStamp(at) });
}

function screenTitle(screen: string, t: TFunction): string {
  if (screen === "council_report") return reportLabel(t, "council_summary");
  if (screen === "route_table") return t("routeTable.title");
  return t("snapshot.page_title");
}

/** The Routes table as the live screen draws it, in the sort it was in,
 *  with no control to change what was stored; compared with today, its rows
 *  carry their marks and the routes only today lists follow them. */
function RouteTableSnapshot({
  report,
  names,
  params,
  comparison,
}: {
  report: RouteTableReport;
  names: RouteNames;
  params: Record<string, unknown>;
  comparison?: Extract<Comparison, { report_type: "route_table" }>;
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
            rows={comparison?.rows ?? report.rows}
            marks={comparison?.marks}
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

function CouncilSnapshot({
  report,
  comparison,
}: {
  report: CouncilReport;
  comparison?: Extract<Comparison, { report_type: "council_summary" }>;
}) {
  const { t } = useTranslation();
  const compare = comparison && { today: comparison.today, changed: comparison.changed };
  return (
    <section>
      <DefinitionMetaBlock definition={report.definition} />
      {report.rows.length > 0 ? <CouncilSummaryBlock report={report} compare={compare} /> : <EmptyState title={t("reports.no_data.title")} />}
    </section>
  );
}
