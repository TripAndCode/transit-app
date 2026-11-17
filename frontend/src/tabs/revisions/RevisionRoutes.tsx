import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useTimetableRevisionRoutes } from "../../api/hooks";
import type { RevisionDayType, RevisionTally, TimetableRevisionRoutes } from "../../api/types";
import { useRouteNames } from "../../api/useRouteNames";
import { DefinitionMetaBlock } from "../../components/DefinitionMetaBlock";
import { ErrorBanner } from "../../components/ErrorBanner";
import { RouteLabel } from "../../components/RouteLabel";
import { SkeletonTable } from "../../components/Skeleton";
import { SHARED_TABLE, td, th } from "../../components/tableStyles";
import { EM_DASH, fixedTenth, formatDate, formatDateRange, formatDuration, formatNumber } from "../../utils/format";
import { serviceClock } from "./revisionModel";

/** How many routes the table lists before "show all". */
const TABLE_ROWS = 50;

function Pct({ tally }: { tally: RevisionTally | null }) {
  const { t } = useTranslation();
  if (tally?.on_time_pct == null) return <>{EM_DASH}</>;
  return (
    <>
      {`${fixedTenth(tally.on_time_pct)}%`}
      {tally.uncertain && <span className="focus-muted"> ({t("reports.wide_margin_mark")})</span>}
    </>
  );
}

function delay(tally: RevisionTally | null): string {
  return tally?.mean_delay_sec == null ? EM_DASH : formatDuration(tally.mean_delay_sec);
}

function gap(sec: number | null): string {
  return sec == null ? EM_DASH : formatDuration(sec);
}

/** What a revision did to each route on one day type: its timetable on the
 *  typical days before and after, and, once whole weeks after it are
 *  aggregated, how its departures ran over the same weeks on each side. */
export function RevisionRoutes({ agencyId, on, dayType }: { agencyId: number; on: string; dayType: RevisionDayType }) {
  const { t } = useTranslation();
  const query = useTimetableRevisionRoutes(agencyId, on, dayType);
  return (
    <section>
      <h2>{t("revisions.routes_title")}</h2>
      {query.isError ? (
        <ErrorBanner error={query.error} onRetry={() => void query.refetch()} />
      ) : query.isPending ? (
        <SkeletonTable />
      ) : (
        <div aria-busy={query.isPlaceholderData} style={{ opacity: query.isPlaceholderData ? 0.5 : 1 }}>
          <RoutesBody key={`${on}:${dayType}`} data={query.data} agencyId={agencyId} />
        </div>
      )}
    </section>
  );
}

/** What the observed side holds, and when it will hold all it can: never a
 *  date the comparison cannot reach. */
function ObservedStatus({ data }: { data: TimetableRevisionRoutes }) {
  const { t } = useTranslation();
  const weeks = data.window;
  const date = data.full_through == null ? "" : formatDate(data.full_through);
  const lines = [
    weeks == null
      ? data.weeks_cap < 1
        ? t("revisions.observed_none")
        : t("revisions.observed_pending", { cap: data.weeks_cap, date })
      : t(data.complete ? "revisions.observed_window" : "revisions.observed_window_partial", {
          weeks: weeks.weeks,
          cap: data.weeks_cap,
          before: formatDateRange(weeks.before_first, weeks.before_last),
          after: formatDateRange(weeks.after_first, weeks.after_last),
          date,
        }),
  ];
  // Under a week, observed_none already says why nothing can be compared.
  if (data.capped_by != null && data.weeks_cap >= 1) lines.push(t(`revisions.observed_capped_${data.capped_by}`));
  return <p className="focus-muted">{lines.join(" ")}</p>;
}

function RoutesBody({ data, agencyId }: { data: TimetableRevisionRoutes; agencyId: number }) {
  const { t } = useTranslation();
  const names = useRouteNames(agencyId);
  const [showAll, setShowAll] = useState(false);
  // An unknown day type is explained once, by the stop comparison above.
  if (!data.available || !data.day_type_known) return null;
  if (data.routes.length === 0) return <p className="focus-muted">{t("revisions.routes_none")}</p>;
  const observed = data.window != null;
  const shown = showAll ? data.routes : data.routes.slice(0, TABLE_ROWS);
  return (
    <>
      <ObservedStatus data={data} />
      {observed && data.classes.length > 0 && (
        <div className="focus-table-wrap">
          <table style={SHARED_TABLE}>
            <thead>
              <tr>
                <th style={th()}>{t("revisions.col.route_class")}</th>
                <th style={th({ align: "right" })}>{t("revisions.col.routes")}</th>
                <th style={th({ align: "right" })}>{t("revisions.col.on_time")}</th>
                <th style={th({ align: "right" })}>{t("revisions.col.mean_delay")}</th>
              </tr>
            </thead>
            <tbody>
              {data.classes.map((c) => (
                <tr key={c.route_class}>
                  <td style={td()}>{t(`revisions.route_class.${c.route_class}`)}</td>
                  <td style={td({ align: "right" })}>{formatNumber(c.routes)}</td>
                  <td style={td({ align: "right" })}>
                    <Pct tally={c.before} /> → <Pct tally={c.after} />
                  </td>
                  <td style={td({ align: "right" })}>
                    {delay(c.before)} → {delay(c.after)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="focus-table-wrap">
        <table style={SHARED_TABLE}>
          <thead>
            <tr>
              <th style={th()}>{t("revisions.col.route")}</th>
              <th style={th()}>{t("revisions.col.route_class")}</th>
              <th style={th({ align: "right" })}>{t("revisions.col.trips")}</th>
              <th style={th({ align: "right" })}>{t("revisions.col.last_trip")}</th>
              <th style={th({ align: "right" })}>{t("revisions.col.longest_gap")}</th>
              {observed && <th style={th({ align: "right" })}>{t("revisions.col.on_time")}</th>}
              {observed && <th style={th({ align: "right" })}>{t("revisions.col.mean_delay")}</th>}
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.route_code}>
                <td style={td()}>
                  <RouteLabel code={r.route_code} names={names} compact />
                </td>
                <td style={td()}>{t(`revisions.route_class.${r.route_class}`)}</td>
                <td style={td({ align: "right" })}>
                  {formatNumber(r.trips_before)} → {formatNumber(r.trips_after)}
                </td>
                <td style={td({ align: "right" })}>
                  {serviceClock(r.last_before)} → {serviceClock(r.last_after)}
                </td>
                <td style={td({ align: "right" })}>
                  {gap(r.longest_gap_before)} → {gap(r.longest_gap_after)}
                  {r.main_stop_id != null && (
                    <span className="focus-muted"> · {r.main_stop_name ?? r.main_stop_id}</span>
                  )}
                </td>
                {observed && (
                  <td style={td({ align: "right" })}>
                    <Pct tally={r.observed_before} /> → <Pct tally={r.observed_after} />
                  </td>
                )}
                {observed && (
                  <td style={td({ align: "right" })}>
                    {delay(r.observed_before)} → {delay(r.observed_after)}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!showAll && data.routes.length > TABLE_ROWS && (
        <div className="focus-actions">
          <button type="button" onClick={() => setShowAll(true)}>
            {t("revisions.show_all_routes", { count: data.routes.length })}
          </button>
        </div>
      )}
      {observed && <p className="focus-muted">{t("revisions.observed_note")}</p>}
      {data.definition && <DefinitionMetaBlock definition={data.definition} />}
    </>
  );
}
