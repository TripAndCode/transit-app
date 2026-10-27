import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { REPORT_ROWS_MAX, useOverviewSummary, useReport } from "../../api/hooks";
import { scopeToQueryString, type Scope } from "../../api/scope";
import { reportHref } from "../../routes/destinations";
import { OnTimeDefinition } from "../DefinitionMetaBlock";
import { EM_DASH, fixedTenth, formatNumber } from "../../utils/format";
import { routeStanding } from "./routeDossier";

/** Every route the lists rank, so this route's place among them is known. */
const WHOLE_LIST = { limit: REPORT_ROWS_MAX } as const;

function Figure({ label, value, note, pending }: { label: string; value: string | null; note?: ReactNode; pending: boolean }) {
  return (
    <div className="route-summary__figure">
      <dt>{label}</dt>
      <dd>
        <strong className="num">{pending ? "…" : (value ?? EM_DASH)}</strong>
        {!pending && note && <span className="focus-muted">{note}</span>}
      </dd>
    </div>
  );
}

/** One route's standing within its agency over the page's scope: its mean
 *  delay and rank, on-time rate, share over five minutes late and share of
 *  all lateness, read from the agency's own route lists and overview so
 *  this page and the Routes lists state the same figures. */
export function RouteSummaryStrip({ agencyId, route, ctx }: { agencyId: number; route: string; ctx: Scope }) {
  const { t } = useTranslation("design");
  const agencyWide = { ...ctx, routes: [] };
  const ranking = useReport(agencyId, "ranking", agencyWide, WHOLE_LIST);
  const onTime = useReport(agencyId, "on_time", agencyWide, WHOLE_LIST);
  const worst = useReport(agencyId, "worst_5min", agencyWide, WHOLE_LIST);
  const overview = useOverviewSummary(agencyId, agencyWide);
  const standing = routeStanding(route, {
    ranking: ranking.data,
    onTime: onTime.data,
    worst: worst.data,
    concentration: overview.data?.concentration,
  });
  const pct = (v: number | null) => (v == null ? null : t("summaryPct", { value: formatNumber(Math.round(v)) }));
  const routeQuery = scopeToQueryString({ ...ctx, routes: [route] });
  const topCount = overview.data?.concentration.top_routes.length ?? 0;
  return (
    <section className="route-summary" aria-label={t("summaryLabel")}>
      <dl className="route-summary__figures">
        <Figure
          label={t("summaryAvg")}
          pending={ranking.isPending || onTime.isPending}
          value={standing.avgMin == null ? null : t("summaryAvgValue", { value: fixedTenth(standing.avgMin) })}
          note={
            standing.rank != null
              ? t("summaryRank", { rank: formatNumber(standing.rank), ranked: formatNumber(standing.ranked ?? 0) })
              : t(standing.avgMin == null ? "summaryNotListed" : "summaryUnranked")
          }
        />
        <Figure label={t("summaryOnTime")} pending={onTime.isPending} value={pct(standing.onTimePct)} />
        <Figure label={t("summaryLate5")} pending={worst.isPending} value={pct(standing.late5Pct)} />
        <Figure
          label={t("summaryShare")}
          pending={overview.isPending}
          value={standing.sharePct == null ? null : t("summaryPct", { value: fixedTenth(standing.sharePct) })}
          note={
            standing.shareRank != null
              ? t("summaryShareRank", { rank: formatNumber(standing.shareRank) })
              : topCount > 0 ? t("summaryShareOutside", { count: topCount }) : null
          }
        />
      </dl>
      {onTime.data && <OnTimeDefinition definition={onTime.data.definition} />}
      <p className="route-summary__links">
        <Link to={reportHref(agencyId, "trend", routeQuery)}>{t("summaryTimeLink")}</Link>
        <Link to={reportHref(agencyId, "dwell_run", routeQuery)}>{t("summaryWhyLink")}</Link>
      </p>
    </section>
  );
}
