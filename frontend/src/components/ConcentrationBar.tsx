// frontend/src/components/ConcentrationBar.tsx
import { useTranslation } from "react-i18next";

import type { OverviewConcentration } from "../api/types";

type Props = { concentration: OverviewConcentration };

// Saturation-by-rank intensity. Top row is fully saturated; rank 5 fades.
const RANK_OPACITY = [1, 0.8, 0.6, 0.45, 0.35];

const MINI_W = 100;
const MINI_H = 30;

export function ConcentrationBar({ concentration }: Props) {
  const { t } = useTranslation();
  if (concentration.top_routes.length === 0) return null;
  const totalTop = concentration.top_routes.reduce(
    (s, r) => s + r.share_pct,
    0,
  );
  const restCount = concentration.rest_route_count ?? 0;

  // Cumulative share curve for the mini Pareto overlay.
  const cumulative: number[] = [];
  let acc = 0;
  for (const r of concentration.top_routes) {
    acc += r.share_pct;
    cumulative.push(acc);
  }
  const maxY = Math.max(100, acc);
  const stepX =
    cumulative.length > 1
      ? (MINI_W - 4) / (cumulative.length - 1)
      : 0;
  const miniCoords = cumulative.map((v, i) => {
    const x = 2 + i * stepX;
    const y = MINI_H - 2 - (v / maxY) * (MINI_H - 6);
    return { x, y, v };
  });
  const miniLine = miniCoords
    .map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`)
    .join(" ");

  return (
    <div className="ov-card">
      <div className="ov-conc-header">
        <p className="ov-card-eyebrow" style={{ margin: 0 }}>
          {t("overview.section_concentration")}
        </p>
        {cumulative.length >= 2 && (
          <svg
            className="ov-conc-mini"
            width={MINI_W}
            height={MINI_H}
            viewBox={`0 0 ${MINI_W} ${MINI_H}`}
            role="img"
            aria-hidden
          >
            <polyline
              fill="none"
              stroke="#b45309"
              strokeOpacity="0.6"
              strokeWidth="1.25"
              strokeLinecap="round"
              strokeLinejoin="round"
              points={miniLine}
            />
            <circle
              cx={miniCoords[miniCoords.length - 1].x}
              cy={miniCoords[miniCoords.length - 1].y}
              r="2"
              fill="#b45309"
            />
            <text
              x={MINI_W - 2}
              y={10}
              fontSize="9"
              fill="#8e8e93"
              textAnchor="end"
            >
              {acc.toFixed(0)}%
            </text>
          </svg>
        )}
      </div>

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
      <p className="ov-pareto-rest" style={{ marginTop: 4 }}>
        {t("overview.concentration_legend", {
          count: concentration.top_routes.length,
          pct: totalTop.toFixed(0),
        })}
      </p>
    </div>
  );
}
