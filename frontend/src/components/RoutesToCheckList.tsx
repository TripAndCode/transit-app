import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";
import { DELAY_THRESHOLDS, delayColor } from "../styles/tokens";
import { useAgencyId } from "../api/useAgencyId";
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

export function RoutesToCheckList({ routes }: Props) {
  const { t } = useTranslation();
  const agencyId = useAgencyId();
  const { search } = useLocation();
  const names = useRouteNames(agencyId);

  const groups = groupBySeverityBand(routes);
  const maxMin = routes.length > 0 ? Math.max(...routes.map((r) => r.avg_min)) : 0;

  return (
    <div>
      <p className="ov-check-section-hd">{t("overview.routes_to_check.title")}</p>
      {/* groups.length, not routes.length: the backend's worst-N list has no
          minimum-delay floor, so a healthy agency's routes can all land in
          the excluded "ok" band, leaving groups empty even when routes isn't —
          gating on routes.length would render this header over blank space. */}
      {groups.length === 0 ? (
        <p className="ov-check-empty">{t("overview.routes_to_check.empty")}</p>
      ) : (
        groups.map((g) => (
          <div key={g.band}>
            <div className="ov-check-band-hd">
              {t(`overview.routes_to_check.band_${g.band}`, { count: g.routes.length, ...BAND_BOUNDS[g.band] })}
            </div>
            {g.routes.map((r) => (
              <Link
                className="ov-check-row"
                key={r.route_code}
                to={agencyId != null ? routeHref(agencyId, r.route_code, search) : "."}
              >
                <span className="ov-check-name">
                  <RouteLabel code={r.route_code} names={names} fallbackName={r.route_short_name} />
                </span>
                <span className="ov-check-track">
                  <span
                    className="ov-check-fill"
                    style={{
                      width: `${maxMin > 0 ? (r.avg_min / maxMin) * 100 : 0}%`,
                      background: delayColor(r.avg_min),
                    }}
                  />
                </span>
                <span className="ov-check-value">{formatMinutes(r.avg_min)}</span>
                <span className="ov-check-arrow" aria-hidden="true">›</span>
              </Link>
            ))}
          </div>
        ))
      )}
    </div>
  );
}
