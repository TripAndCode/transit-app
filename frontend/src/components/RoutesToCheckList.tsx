import { useTranslation } from "react-i18next";
import { delayColor } from "../styles/tokens";
import { useRangeContext } from "../api/rangeContext";
import { groupBySeverityBand } from "./routesToCheckBands";
import type { OverviewTopDelayedRoute } from "../api/types";

type Props = {
  routes: OverviewTopDelayedRoute[];
};

export function RoutesToCheckList({ routes }: Props) {
  const { t } = useTranslation();
  const [, update] = useRangeContext();

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
              <span>{t(g.labelKey)}</span>
              <span className="ov-check-band-count">{g.routes.length}</span>
            </div>
            {g.routes.map((r) => (
              <div
                className="ov-check-row"
                key={r.route_code}
                role="button"
                tabIndex={0}
                onClick={() => update({ routes: [r.route_code] })}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    update({ routes: [r.route_code] });
                  }
                }}
              >
                <span className="ov-check-name">
                  {r.route_short_name || r.route_code}
                  {/* "" is a real value the backend can return for an unnamed
                      route, not just null/undefined — `||` (not `??`) so an
                      empty string falls back to the code too, instead of
                      rendering a blank row. */}
                  {r.route_short_name && <span className="ov-check-name-code"> ({r.route_code})</span>}
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
                <span className="ov-check-value">{r.avg_min.toFixed(1)}</span>
                <span className="ov-check-arrow" aria-hidden="true">›</span>
              </div>
            ))}
          </div>
        ))
      )}
    </div>
  );
}
