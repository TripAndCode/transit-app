import { useRef, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { delayColor } from "../styles/tokens";
import { useCountUp } from "../hooks/useCountUp";
import { useFlipRows } from "../hooks/useFlipRows";
import { useScope } from "../api/scope";
import { useAgencyId } from "../api/useAgencyId";
import { useRouteNames } from "../api/useRouteNames";
import { RouteLabel } from "./RouteLabel";
import { groupBySeverityBand } from "./routesToCheckBands";
import type { OverviewTopDelayedRoute } from "../api/types";

type Props = {
  routes: OverviewTopDelayedRoute[];
};

/** A row's figure: printed as it is on first paint, travelling from the
 *  previous value when the period or the data changes. */
function CheckValue({ value }: { value: number }) {
  const shown = useCountUp(value, { decimals: 1 });
  return <span className="ov-check-value num">{shown.toFixed(1)}</span>;
}

export function RoutesToCheckList({ routes }: Props) {
  const { t } = useTranslation();
  const [, update] = useScope();
  const names = useRouteNames(useAgencyId());

  const groups = groupBySeverityBand(routes);
  const maxMin = routes.length > 0 ? Math.max(...routes.map((r) => r.avg_min)) : 0;

  // The ranked identity is what can change the order; a refetch that changed
  // nothing produces the same string and no FLIP. Band headers between rows
  // do not matter: useFlipRows measures only the [data-flip-key] rows.
  const listRef = useRef<HTMLDivElement | null>(null);
  useFlipRows(listRef, groups.flatMap((g) => g.routes.map((r) => r.route_code)).join(","));

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
                data-flip-key={r.route_code}
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
                  <RouteLabel code={r.route_code} names={names} fallbackName={r.route_short_name} />
                </span>
                <span className="ov-check-track">
                  <span
                    className="ov-check-fill"
                    style={
                      {
                        "--w": maxMin > 0 ? r.avg_min / maxMin : 0,
                        background: delayColor(r.avg_min),
                      } as CSSProperties
                    }
                  />
                </span>
                <CheckValue value={r.avg_min} />
                <span className="ov-check-arrow" aria-hidden="true">›</span>
              </div>
            ))}
          </div>
        ))
      )}
    </div>
  );
}
