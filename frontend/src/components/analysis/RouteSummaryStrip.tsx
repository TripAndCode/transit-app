import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { REPORT_ROWS_MAX, useOverviewSummary, useReport } from "../../api/hooks";
import { scopeToQueryString, type Scope } from "../../api/scope";
import type { OverviewConcentration, ReportFigures } from "../../api/types";
import { reportHref } from "../../routes/destinations";
import { OnTimeDefinition } from "../DefinitionMetaBlock";
import { EM_DASH, fixedTenth, formatNumber } from "../../utils/format";
import { routeStanding } from "./routeDossier";
import { RouteReferenceNote } from "./RouteReference";
import "../../styles/focusedAnalysis.css";

/** Every route the lists rank, so this route's place among them is known. */
const WHOLE_LIST = { limit: REPORT_ROWS_MAX } as const;

/** The agency's on-time list the strip reads the route's rate from. The
 *  route page reads it too, for the conditions the rate used, and shares
 *  this one request. */
export function useAgencyOnTime(agencyId: number | null, ctx: Scope) {
  return useReport(agencyId, "on_time", { ...ctx, routes: [] }, WHOLE_LIST);
}

function Figure({
  label,
  value,
  note,
  reference,
  pending,
}: {
  label: string;
  value: string | null;
  note?: ReactNode;
  /** The figure's trailing reference, under its note. */
  reference?: ReactNode;
  pending: boolean;
}) {
  return (
    <div className="route-summary__figure">
      <dt>{label}</dt>
      <dd>
        <strong className="num">{pending ? "…" : (value ?? EM_DASH)}</strong>
        {!pending && note && <span className="focus-muted">{note}</span>}
        {!pending && reference}
      </dd>
    </div>
  );
}

export type RouteSummarySources = {
  ranking?: ReportFigures;
  onTime?: ReportFigures;
  worst?: ReportFigures;
  concentration?: OverviewConcentration;
};

/** Which of the sources are still on their way; a frozen page has none. */
type RouteSummaryPending = { ranking: boolean; onTime: boolean; worst: boolean; overview: boolean };

const NONE_PENDING: RouteSummaryPending = { ranking: false, onTime: false, worst: false, overview: false };

/** One route's standing within its agency over the page's scope: its mean
 *  delay and rank, on-time rate, share over five minutes late and share of
 *  all lateness, read from the agency's own route lists and overview so
 *  this page and the Routes lists state the same figures. The mean delay and
 *  on-time rate each name the route's own reference beside them, from the
 *  on-time list the rate is read from. */
export function RouteSummaryStrip({ agencyId, route, ctx }: { agencyId: number; route: string; ctx: Scope }) {
  const { t } = useTranslation("design");
  const agencyWide = { ...ctx, routes: [] };
  const ranking = useReport(agencyId, "ranking", agencyWide, WHOLE_LIST);
  const onTime = useAgencyOnTime(agencyId, ctx);
  const worst = useReport(agencyId, "worst_5min", agencyWide, WHOLE_LIST);
  const overview = useOverviewSummary(agencyId, agencyWide);
  const routeQuery = scopeToQueryString({ ...ctx, routes: [route] });
  return (
    <RouteSummaryFigures
      route={route}
      timeBand={ctx.time_band}
      sources={{ ranking: ranking.data, onTime: onTime.data, worst: worst.data, concentration: overview.data?.concentration }}
      pending={{ ranking: ranking.isPending, onTime: onTime.isPending, worst: worst.isPending, overview: overview.isPending }}
      links={
        <p className="route-summary__links">
          <Link to={reportHref(agencyId, "trend", routeQuery)}>{t("summaryTimeLink")}</Link>
          <Link to={reportHref(agencyId, "dwell_run", routeQuery)}>{t("summaryWhyLink")}</Link>
        </p>
      }
    />
  );
}

/** The strip's figures from the responses it reads, fetched by the live page
 *  or stored by a snapshot. `links` lead on to the route's live reports, which
 *  a frozen page has none of. */
export function RouteSummaryFigures({
  route,
  timeBand: scopeTimeBand,
  sources,
  pending = NONE_PENDING,
  links,
}: {
  route: string;
  timeBand: string;
  sources: RouteSummarySources;
  pending?: RouteSummaryPending;
  links?: ReactNode;
}) {
  const { t } = useTranslation("design");
  const { onTime } = sources;
  const standing = routeStanding(route, sources);
  const pct = (v: number | null) => (v == null ? null : t("summaryPct", { value: formatNumber(Math.round(v)) }));
  const topCount = sources.concentration?.top_routes.length ?? 0;
  const refs = onTime?.reference ?? null;
  const timeBand = onTime?.ctx?.time_band ?? scopeTimeBand;
  return (
    <section className="route-summary" aria-label={t("summaryLabel")}>
      <dl className="route-summary__figures">
        <Figure
          label={t("summaryAvg")}
          pending={pending.ranking || pending.onTime}
          value={standing.avgMin == null ? null : t("summaryAvgValue", { value: fixedTenth(standing.avgMin) })}
          note={
            standing.rank != null
              ? t("summaryRank", { rank: formatNumber(standing.rank), ranked: formatNumber(standing.ranked ?? 0) })
              : t(standing.avgMin == null ? "summaryNotListed" : "summaryUnranked")
          }
          reference={
            refs && <RouteReferenceNote refs={refs} route={route} figure="avg" value={standing.avgMin} timeBand={timeBand} />
          }
        />
        <Figure
          label={t("summaryOnTime")}
          pending={pending.onTime}
          value={pct(standing.onTimePct)}
          reference={
            refs && (
              <RouteReferenceNote refs={refs} route={route} figure="on_time" value={standing.onTimePct} timeBand={timeBand} primary />
            )
          }
        />
        <Figure label={t("summaryLate5")} pending={pending.worst} value={pct(standing.late5Pct)} />
        <Figure
          label={t("summaryShare")}
          pending={pending.overview}
          value={standing.sharePct == null ? null : t("summaryPct", { value: fixedTenth(standing.sharePct) })}
          note={
            standing.shareRank != null
              ? t("summaryShareRank", { rank: formatNumber(standing.shareRank) })
              : topCount > 0 ? t("summaryShareOutside", { count: topCount }) : null
          }
        />
      </dl>
      {onTime && <OnTimeDefinition definition={onTime.definition} />}
      {links}
    </section>
  );
}
