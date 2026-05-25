import { useTranslation } from "react-i18next";

import type { OverviewConcentration } from "../api/types";

type Props = { concentration: OverviewConcentration };

export function ConcentrationBar({ concentration }: Props) {
  const { t } = useTranslation();
  if (concentration.top_routes.length === 0) return null;
  const totalTop = concentration.top_routes.reduce((s, r) => s + r.share_pct, 0);
  const restCount = concentration.rest_route_count ?? 0;
  return (
    <div>
      <div className="ov-label">{t("overview.section_concentration")}</div>
      {concentration.top_routes.map((r) => (
        <div className="ov-pareto-row" key={r.route_code}>
          <div className="ov-pareto-label">
            {r.route_short_name ? `${r.route_short_name} (${r.route_code})` : r.route_code}
          </div>
          <div className="ov-pareto-track">
            <div
              className="ov-pareto-fill ov-anim-grow-x"
              style={{ width: `${r.share_pct}%` }}
            />
          </div>
          <div className="ov-pareto-pct">{r.share_pct.toFixed(1)}%</div>
        </div>
      ))}
      {concentration.rest_share_pct > 0 && restCount > 0 && (
        <p className="ov-pareto-rest">
          {t("overview.concentration_rest", {
            count: restCount,
            rest: concentration.rest_share_pct.toFixed(1),
          })}
        </p>
      )}
      <p className="ov-conc-legend">
        {t("overview.concentration_legend", {
          count: concentration.top_routes.length,
          pct: totalTop.toFixed(0),
        })}
      </p>
    </div>
  );
}
