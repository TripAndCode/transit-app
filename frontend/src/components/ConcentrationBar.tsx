import { useTranslation } from "react-i18next";

import type { OverviewConcentration } from "../api/types";

type Props = { concentration: OverviewConcentration };

export function ConcentrationBar({ concentration }: Props) {
  const { t } = useTranslation();
  if (concentration.top_routes.length === 0) return null;
  const totalTop = concentration.top_routes.reduce((s, r) => s + r.share_pct, 0);
  return (
    <div>
      <div className="ov-label">{t("overview.section_concentration")}</div>
      <div className="ov-conc-bar">
        {concentration.top_routes.map((r) => (
          <div
            key={r.route_code}
            className="ov-conc-seg ov-anim-grow-x"
            style={{ width: `${r.share_pct}%` }}
            title={`${r.route_short_name ?? r.route_code} — ${r.share_pct}%`}
          />
        ))}
        {concentration.rest_share_pct > 0 && (
          <div
            className="ov-conc-seg rest ov-anim-grow-x"
            style={{ width: `${concentration.rest_share_pct}%` }}
          />
        )}
      </div>
      <p className="ov-conc-legend">
        {t("overview.concentration_legend", {
          count: concentration.top_routes.length,
          pct: totalTop.toFixed(0),
        })}
      </p>
    </div>
  );
}
