import { useTranslation } from "react-i18next";
import { delayColor } from "../styles/tokens";
import type { OverviewTopDelayedRoute } from "../api/types";

type Props = {
  routes: OverviewTopDelayedRoute[];
};

export function RoutesToCheckList({ routes }: Props) {
  const { t } = useTranslation();

  return (
    <div className="ov-card">
      <p className="ov-card-eyebrow">{t("overview.routes_to_check.title")}</p>
      {routes.length === 0 ? (
        <p className="ov-pareto-rest">{t("overview.routes_to_check.empty")}</p>
      ) : (
        (() => {
          const maxMin = Math.max(...routes.map((r) => r.avg_min));
          return routes.map((r) => (
            <div className="ov-pareto-row" key={r.route_code}>
              <div className="ov-pareto-label">
                {r.route_short_name ? `${r.route_short_name} (${r.route_code})` : r.route_code}
              </div>
              <div className="ov-pareto-track">
                <div
                  className="ov-pareto-fill"
                  style={{
                    width: `${maxMin > 0 ? (r.avg_min / maxMin) * 100 : 0}%`,
                    background: delayColor(r.avg_min),
                  }}
                />
              </div>
              <div className="ov-pareto-pct">{r.avg_min.toFixed(1)}</div>
            </div>
          ));
        })()
      )}
    </div>
  );
}
