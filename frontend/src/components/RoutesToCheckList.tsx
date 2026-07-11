import { useTranslation } from "react-i18next";
import { delayColor } from "../styles/tokens";
import { useRangeContext } from "../api/rangeContext";
import type { OverviewTopDelayedRoute } from "../api/types";

type Props = {
  routes: OverviewTopDelayedRoute[];
};

export function RoutesToCheckList({ routes }: Props) {
  const { t } = useTranslation();
  const [, update] = useRangeContext();

  return (
    <div>
      <p className="ov-check-section-hd">{t("overview.routes_to_check.title")}</p>
      {routes.length === 0 ? (
        <p className="ov-check-empty">{t("overview.routes_to_check.empty")}</p>
      ) : (
        (() => {
          const maxMin = Math.max(...routes.map((r) => r.avg_min));
          return routes.map((r) => (
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
              <span className="ov-check-code">{r.route_code}</span>
              <span className="ov-check-name">{r.route_short_name ?? r.route_code}</span>
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
          ));
        })()
      )}
    </div>
  );
}
