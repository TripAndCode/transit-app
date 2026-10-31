import { useRef, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";
import { delayRampVar } from "../styles/tokens";
import { useAgencyId } from "../api/useAgencyId";
import { useCountUp } from "../hooks/useCountUp";
import { useFlipRows } from "../hooks/useFlipRows";
import { useRouteNames } from "../api/useRouteNames";
import { reportHref, routeHref } from "../routes/destinations";
import { fixedTenth, formatMinutes } from "../utils/format";
import { windowDays } from "./overview/storySentence";
import { RouteLabel } from "./RouteLabel";
import { DelayScaleLegend } from "./charts/DelayScaleLegend";
import { BAND_BOUNDS, groupBySeverityBand } from "./routesToCheckBands";
import type { OverviewBandCounts, OverviewTopDelayedRoute } from "../api/types";

type Props = {
  routes: OverviewTopDelayedRoute[];
  /** Each band's routes among every route the list ranks (`routeCount`), not
   *  only the listed ones: the list is the worst few, so whichever band it
   *  runs out in is truncated. */
  bandCounts: OverviewBandCounts;
  /** The routes the list ranks: those observed at least `minSamples` times
   *  in its window. */
  routeCount: number;
  minSamples: number;
  /** The days the list counts, as ISO dates: the headline's window, not the
   *  whole period. */
  windowFrom: string;
  windowTo: string;
  /** Those days as text. */
  range: string;
};

/** A change in minutes with its sign; zero has none. */
function signedMinutes(delta: number): string {
  const tenth = fixedTenth(Math.abs(delta));
  if (tenth === fixedTenth(0)) return tenth;
  return `${delta > 0 ? "+" : "\u2212"}${tenth}`;
}

/** The Routes table over the list's own days at its default floor, sorted
 *  by average delay worst first, so it lists the same routes. */
function routeTableHref(agencyId: number, search: string, from: string, to: string): string {
  const params = new URLSearchParams(search);
  params.set("from", from);
  params.set("to", to);
  params.delete("sparse");
  return reportHref(agencyId, "ranking", params.toString());
}

/** A row's figure: printed as it is on first paint, travelling from the
 *  previous value when the data changes while the list stays mounted (a
 *  refetch, or a period still cached). A period that has to load first
 *  remounts the list behind its skeleton, so that one prints. */
function CheckValue({ value }: { value: number }) {
  const shown = useCountUp(value, { decimals: 1 });
  return <span className="ov-check-value num">{formatMinutes(shown)}</span>;
}

export function RoutesToCheckList({ routes, bandCounts, routeCount, minSamples, windowFrom, windowTo, range }: Props) {
  const { t } = useTranslation();
  const agencyId = useAgencyId();
  const { search } = useLocation();
  const names = useRouteNames(agencyId);

  const groups = groupBySeverityBand(routes);
  const days = windowDays(windowFrom, windowTo);
  // A week's window is compared with the week before it.
  const changeLabel = (delta: number) =>
    days === 7
      ? t("overview.routes_to_check.change_week", { delta: signedMinutes(delta) })
      : t("overview.routes_to_check.change_days", { count: days, delta: signedMinutes(delta) });
  const maxMin = routes.length > 0 ? Math.max(...routes.map((r) => r.avg_min)) : 0;

  // What can move a row: its rank, and the band headers above it, which
  // appear or go as routes cross a threshold; headers are keyed too, so they
  // slide with the rows. A refetch that changed neither produces the same
  // string and no FLIP.
  const listRef = useRef<HTMLDivElement | null>(null);
  useFlipRows(listRef, groups.map((g) => `${g.band}:${g.routes.map((r) => r.route_code).join(",")}`).join("|"));

  return (
    <div ref={listRef}>
      <p className="ov-check-section-hd">{t("overview.routes_to_check.title", { count: days, range })}</p>
      {routeCount > 0 && (
        <p className="ov-check-scope">
          {t("overview.routes_to_check.scope", { count: routeCount, min: minSamples })}
        </p>
      )}
      {/* groups.length, not routes.length: the backend's worst-N list has no
          minimum-delay floor, so a healthy agency's routes can all land in
          the excluded "ok" band, leaving groups empty even when routes isn't —
          gating on routes.length would render this header over blank space. */}
      {groups.length === 0 ? (
        <p className="ov-check-empty">{t("overview.routes_to_check.empty")}</p>
      ) : (
        // Headers and rows are siblings in one keyed list, not rows nested
        // per band: a route crossing a threshold keeps its node, so its
        // figure counts and its bar slides instead of remounting.
        <div className="ov-check-list">
          {groups.flatMap((g) => [
            <div key={`band:${g.band}`} className="ov-check-band-hd" data-flip-key={`band:${g.band}`}>
              {bandCounts[g.band].count > g.routes.length
                ? t(`overview.routes_to_check.band_${g.band}_truncated`, {
                    count: bandCounts[g.band].count,
                    shown: g.routes.length,
                    ...BAND_BOUNDS[g.band],
                  })
                : t(`overview.routes_to_check.band_${g.band}`, { count: g.routes.length, ...BAND_BOUNDS[g.band] })}
            </div>,
            ...g.routes.map((r) => (
              <Link
                className="ov-check-row"
                key={`route:${r.route_code}`}
                data-flip-key={r.route_code}
                to={agencyId != null ? routeHref(agencyId, r.route_code, search) : "."}
              >
                <span className="ov-check-name">
                  <RouteLabel code={r.route_code} names={names} fallbackName={r.route_short_name} plateColumn />
                </span>
                <span className="ov-check-track">
                  <span
                    className="ov-check-fill"
                    style={
                      {
                        "--bar-share": maxMin > 0 ? r.avg_min / maxMin : 0,
                        background: delayRampVar(r.avg_min),
                      } as CSSProperties
                    }
                  />
                </span>
                <span className="ov-check-figures">
                  <CheckValue value={r.avg_min} />
                  {r.delta_min != null && <span className="ov-check-change num">{changeLabel(r.delta_min)}</span>}
                </span>
                <span className="ov-check-arrow" aria-hidden="true">›</span>
              </Link>
            )),
          ])}
        </div>
      )}
      {/* The bands cut at DELAY_THRESHOLDS (1.5/3/5 min) and the scale at its
          own finer steps, so one band can hold two colours: the legend says
          which minutes each colour holds. */}
      {groups.length > 0 && (
        <div className="ov-check-legend">
          <DelayScaleLegend />
        </div>
      )}
      {agencyId != null && routeCount > routes.length && (
        <Link className="ov-check-more" to={routeTableHref(agencyId, search, windowFrom, windowTo)}>
          {t("overview.routes_to_check.see_all", { count: routeCount })}
        </Link>
      )}
    </div>
  );
}
