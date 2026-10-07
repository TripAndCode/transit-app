import { useRef, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";
import { DELAY_THRESHOLDS, delayColor } from "../styles/tokens";
import { useAgencyId } from "../api/useAgencyId";
import { useCountUp } from "../hooks/useCountUp";
import { useFlipRows } from "../hooks/useFlipRows";
import { useRouteNames } from "../api/useRouteNames";
import { routeHref } from "../routes/destinations";
import { formatMinutes } from "../utils/format";
import { RouteLabel } from "./RouteLabel";
import { groupBySeverityBand } from "./routesToCheckBands";
import type { OverviewTopDelayedRoute } from "../api/types";

type Props = {
  routes: OverviewTopDelayedRoute[];
};

/** Each band's bounds, read from the same thresholds the colour ramp uses so
 *  the header can't state a range the band does not hold. */
const BAND_BOUNDS = {
  severe: { min: DELAY_THRESHOLDS.severe },
  moderate: { min: DELAY_THRESHOLDS.moderate, max: DELAY_THRESHOLDS.severe },
  mild: { min: DELAY_THRESHOLDS.mild, max: DELAY_THRESHOLDS.moderate },
} as const;

/** A row's figure: printed as it is on first paint, travelling from the
 *  previous value when the data changes while the list stays mounted (a
 *  refetch, or a period still cached). A period that has to load first
 *  remounts the list behind its skeleton, so that one prints. */
function CheckValue({ value }: { value: number }) {
  const shown = useCountUp(value, { decimals: 1 });
  return <span className="ov-check-value num">{formatMinutes(shown)}</span>;
}

export function RoutesToCheckList({ routes }: Props) {
  const { t } = useTranslation();
  const agencyId = useAgencyId();
  const { search } = useLocation();
  const names = useRouteNames(agencyId);

  const groups = groupBySeverityBand(routes);
  const maxMin = routes.length > 0 ? Math.max(...routes.map((r) => r.avg_min)) : 0;

  // What can move a row: its rank, and the band headers above it, which
  // appear or go as routes cross a threshold. A refetch that changed neither
  // produces the same string and no FLIP.
  const listRef = useRef<HTMLDivElement | null>(null);
  useFlipRows(listRef, groups.map((g) => `${g.band}:${g.routes.map((r) => r.route_code).join(",")}`).join("|"));

  return (
    <div ref={listRef}>
      <p className="ov-check-section-hd">{t("overview.routes_to_check.title")}</p>
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
        groups.flatMap((g) => [
          <div key={`band:${g.band}`} className="ov-check-band-hd">
            {t(`overview.routes_to_check.band_${g.band}`, { count: g.routes.length, ...BAND_BOUNDS[g.band] })}
          </div>,
          ...g.routes.map((r) => (
            <Link
              className="ov-check-row"
              key={`route:${r.route_code}`}
              data-flip-key={r.route_code}
              to={agencyId != null ? routeHref(agencyId, r.route_code, search) : "."}
            >
              <span className="ov-check-name">
                <RouteLabel code={r.route_code} names={names} fallbackName={r.route_short_name} />
              </span>
              <span className="ov-check-track">
                <span
                  className="ov-check-fill"
                  style={
                    {
                      "--check-share": maxMin > 0 ? r.avg_min / maxMin : 0,
                      background: delayColor(r.avg_min),
                    } as CSSProperties
                  }
                />
              </span>
              <CheckValue value={r.avg_min} />
              <span className="ov-check-arrow" aria-hidden="true">›</span>
            </Link>
          )),
        ])
      )}
    </div>
  );
}
