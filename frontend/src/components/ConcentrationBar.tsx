// frontend/src/components/ConcentrationBar.tsx
import { useTranslation } from "react-i18next";

import type { OverviewConcentration, OverviewMovers } from "../api/types";
import { InlineSparkline } from "./InlineSparkline";

type Props = {
  concentration: OverviewConcentration;
  movers?: OverviewMovers;
};

// Saturation-by-rank intensity. Top row is fully saturated; rank 5 fades.
const RANK_OPACITY = [1, 0.8, 0.6, 0.45, 0.35];

export function ConcentrationBar({ concentration, movers }: Props) {
  const { t } = useTranslation();
  if (concentration.top_routes.length === 0) return null;

  const totalTop = concentration.top_routes.reduce(
    (s, r) => s + r.share_pct,
    0,
  );
  const restCount = concentration.rest_route_count ?? 0;

  // Case B — single-route spotlight
  if (concentration.top_routes.length === 1) {
    const only = concentration.top_routes[0];
    const displayName = only.route_short_name
      ? `${only.route_short_name} (${only.route_code})`
      : only.route_code;

    // Try to pluck the matching mover for a tonal sparkline
    const matchInWorse = movers?.worse.find((m) => m.route_code === only.route_code);
    const matchInBetter = movers?.better.find((m) => m.route_code === only.route_code);
    const match = matchInWorse ?? matchInBetter ?? null;
    const sparkPoints =
      match && match.sparkline_points.length >= 2 ? match.sparkline_points : null;

    return (
      <div className="ov-card">
        <p className="ov-card-eyebrow">
          {t("overview.section_concentration")}
        </p>
        <p className="ov-conc-spotlight">
          {t("overview.concentration.single_route", { name: displayName })}
        </p>
        {sparkPoints && (
          <div className="ov-conc-spotlight-spark">
            <InlineSparkline
              points={sparkPoints}
              width={320}
              height={48}
              accent="#475569"
              forceAccent
              showLabels={false}
              showEndDot
            />
          </div>
        )}
      </div>
    );
  }

  // Case A — multi-route Pareto
  return (
    <div className="ov-card">
      <p className="ov-card-eyebrow">
        {t("overview.section_concentration")}
      </p>
      <p className="ov-conc-headline">
        {t("overview.concentration_legend", {
          count: concentration.top_routes.length,
          pct: totalTop.toFixed(0),
        })}
      </p>

      {concentration.top_routes.map((r, idx) => {
        const opacity = RANK_OPACITY[Math.min(idx, RANK_OPACITY.length - 1)];
        return (
          <div className="ov-pareto-row" key={r.route_code}>
            <div className="ov-pareto-label">
              {r.route_short_name
                ? `${r.route_short_name} (${r.route_code})`
                : r.route_code}
            </div>
            <div className="ov-pareto-track">
              <div
                className="ov-pareto-fill ov-anim-grow-x"
                style={{
                  width: `${r.share_pct}%`,
                  background: "#b45309",
                  opacity,
                }}
              />
            </div>
            <div className="ov-pareto-pct">{r.share_pct.toFixed(1)}%</div>
          </div>
        );
      })}
      {concentration.rest_share_pct > 0 && restCount > 0 && (
        <p className="ov-pareto-rest">
          {t("overview.concentration_rest", {
            count: restCount,
            rest: concentration.rest_share_pct.toFixed(1),
          })}
        </p>
      )}
    </div>
  );
}
