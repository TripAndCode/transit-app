// frontend/src/components/ConcentrationBar.tsx
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import type { OverviewConcentration, OverviewMovers } from "../api/types";
import { InlineSparkline } from "./InlineSparkline";

type Props = {
  concentration: OverviewConcentration;
  movers?: OverviewMovers;
  /** How many top routes to render. Card variant: 5 (default).
   *  Modal variant: 20 (matches backend top-N). */
  limit?: number;
  /** "card" (default) wraps in .ov-card; "modal" drops chrome and
   *  appends a Lorenz curve. */
  variant?: "card" | "modal";
  /** When set, the card becomes clickable. */
  onClick?: () => void;
};

// Saturation-by-rank intensity. Top row is fully saturated; rank 5 fades.
const RANK_OPACITY = [1, 0.8, 0.6, 0.45, 0.35];

export function ConcentrationBar({
  concentration,
  movers,
  limit = 5,
  variant = "card",
  onClick,
}: Props) {
  const { t } = useTranslation();
  if (concentration.top_routes.length === 0) return null;

  const visibleRoutes = concentration.top_routes.slice(0, limit);
  const totalTop = visibleRoutes.reduce((s, r) => s + r.share_pct, 0);
  // When the slice doesn't cover all top routes, fold the unsliced
  // remainder into rest_share_pct + rest_route_count for an honest
  // "the remaining N routes carry M%" line.
  const slicedTail = concentration.top_routes.slice(limit);
  const slicedTailShare = slicedTail.reduce((s, r) => s + r.share_pct, 0);
  const restCount =
    (concentration.rest_route_count ?? 0) + slicedTail.length;
  const restSharePct = concentration.rest_share_pct + slicedTailShare;

  const clickable = !!onClick;
  const wrapperClass =
    variant === "modal"
      ? "ov-conc-modal"
      : `ov-card${clickable ? " ov-clickable" : ""}`;
  const interactiveProps = clickable
    ? {
        tabIndex: 0,
        role: "button",
        onClick,
        onKeyDown: (e: React.KeyboardEvent) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onClick?.();
          }
        },
      }
    : {};

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
      <div className={wrapperClass} {...interactiveProps}>
        {variant !== "modal" && (
          <p className="ov-card-eyebrow">
            {t("overview.section_concentration")}
          </p>
        )}
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

  return (
    <div className={wrapperClass} {...interactiveProps}>
      {variant !== "modal" && (
        <p className="ov-card-eyebrow">
          {t("overview.section_concentration")}
        </p>
      )}
      <p className="ov-conc-headline">
        {t("overview.concentration_legend", {
          count: visibleRoutes.length,
          pct: totalTop.toFixed(0),
        })}
      </p>

      {visibleRoutes.map((r, idx) => {
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
      {restSharePct > 0 && restCount > 0 && (
        <p className="ov-pareto-rest">
          {t("overview.concentration_rest", {
            count: restCount,
            rest: restSharePct.toFixed(1),
          })}
        </p>
      )}
      {variant === "modal" && (
        <LorenzCurve
          topRoutes={concentration.top_routes}
          restSharePct={concentration.rest_share_pct}
          restRouteCount={concentration.rest_route_count ?? 0}
        />
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Lorenz curve overlay — rendered in the concentration modal only.
//
// The curve plots cumulative % of routes (x) vs cumulative % of delay (y),
// from the most-concentrated route at the right down to the least at the
// left. The diagonal (perfect equality) is shown for reference. A steeply
// concave curve indicates that a small number of routes account for most
// of the lateness — the textbook Pareto signal.
// --------------------------------------------------------------------------

const LZ_W = 480;
const LZ_H = 240;
const LZ_PAD_LEFT = 36;
const LZ_PAD_BOTTOM = 28;
const LZ_PAD_TOP = 12;
const LZ_PAD_RIGHT = 12;

function LorenzCurve({
  topRoutes,
  restSharePct,
  restRouteCount,
}: {
  topRoutes: OverviewConcentration["top_routes"];
  restSharePct: number;
  restRouteCount: number;
}) {
  const { t } = useTranslation();
  const { path, ticks20, share20Pct } = useMemo(() => {
    // Build ascending shares: rest is one aggregate bucket (the
    // long tail of small routes), followed by top_routes sorted
    // ascending so the curve climbs from flat -> steep.
    const shares: number[] = [];
    if (restRouteCount > 0 && restSharePct > 0) {
      // Treat each rest-route as carrying an equal slice; this
      // smooths the long tail rather than spiking it.
      const per = restSharePct / restRouteCount;
      for (let i = 0; i < restRouteCount; i++) shares.push(per);
    }
    const ascendingTop = [...topRoutes]
      .map((r) => r.share_pct)
      .sort((a, b) => a - b);
    shares.push(...ascendingTop);
    const totalRoutes = shares.length;
    if (totalRoutes === 0) return { path: "", ticks20: null, share20Pct: 0 };
    const grand = shares.reduce((s, v) => s + v, 0) || 1;

    const xs: number[] = [0];
    const ys: number[] = [0];
    let cum = 0;
    for (let i = 0; i < totalRoutes; i++) {
      cum += shares[i];
      xs.push(((i + 1) / totalRoutes) * 100);
      ys.push((cum / grand) * 100);
    }

    const innerW = LZ_W - LZ_PAD_LEFT - LZ_PAD_RIGHT;
    const innerH = LZ_H - LZ_PAD_TOP - LZ_PAD_BOTTOM;
    const toX = (xp: number) => LZ_PAD_LEFT + (xp / 100) * innerW;
    // Y axis is inverted: 100% at top, 0% at bottom.
    const toY = (yp: number) => LZ_PAD_TOP + (1 - yp / 100) * innerH;
    const pathStr = xs
      .map((xp, i) => `${i === 0 ? "M" : "L"} ${toX(xp).toFixed(1)},${toY(ys[i]).toFixed(1)}`)
      .join(" ");

    // "top 20% of routes carry X% of delay" — read the curve at x=80%
    // from the right side (since we sorted ascending, the rightmost
    // 20% is the most concentrated).
    const cutoff = 80;
    let interpY = 0;
    for (let i = 1; i < xs.length; i++) {
      if (xs[i] >= cutoff) {
        const t = (cutoff - xs[i - 1]) / (xs[i] - xs[i - 1] || 1);
        interpY = ys[i - 1] + t * (ys[i] - ys[i - 1]);
        break;
      }
    }
    return {
      path: pathStr,
      ticks20: { x: toX(cutoff), y: toY(interpY) },
      share20Pct: 100 - interpY,
    };
  }, [topRoutes, restSharePct, restRouteCount]);

  if (!path) return null;
  const innerW = LZ_W - LZ_PAD_LEFT - LZ_PAD_RIGHT;
  const innerH = LZ_H - LZ_PAD_TOP - LZ_PAD_BOTTOM;
  const diagonal = `M ${LZ_PAD_LEFT},${LZ_PAD_TOP + innerH} L ${LZ_PAD_LEFT + innerW},${LZ_PAD_TOP}`;
  return (
    <div className="ov-lorenz-wrap">
      <svg
        width="100%"
        viewBox={`0 0 ${LZ_W} ${LZ_H}`}
        role="img"
        aria-label={t("overview.concentration.lorenz_label")}
        style={{ display: "block" }}
      >
        {/* Axes */}
        <line
          x1={LZ_PAD_LEFT}
          y1={LZ_PAD_TOP}
          x2={LZ_PAD_LEFT}
          y2={LZ_PAD_TOP + innerH}
          stroke="#e5e7eb"
          strokeWidth="1"
        />
        <line
          x1={LZ_PAD_LEFT}
          y1={LZ_PAD_TOP + innerH}
          x2={LZ_PAD_LEFT + innerW}
          y2={LZ_PAD_TOP + innerH}
          stroke="#e5e7eb"
          strokeWidth="1"
        />
        {/* Equality diagonal */}
        <path
          d={diagonal}
          stroke="#cbd5e1"
          strokeWidth="1"
          strokeDasharray="3 4"
          fill="none"
        />
        {/* Lorenz curve */}
        <path d={path} stroke="#b45309" strokeWidth="1.8" fill="none" />
        {/* 80% marker */}
        {ticks20 && (
          <g>
            <line
              x1={ticks20.x}
              y1={LZ_PAD_TOP + innerH}
              x2={ticks20.x}
              y2={ticks20.y}
              stroke="#94a3b8"
              strokeWidth="0.8"
              strokeDasharray="2 3"
            />
            <circle cx={ticks20.x} cy={ticks20.y} r="3" fill="#b45309" />
          </g>
        )}
        {/* X tick labels */}
        {[0, 50, 100].map((p) => (
          <text
            key={`xt-${p}`}
            x={LZ_PAD_LEFT + (p / 100) * innerW}
            y={LZ_PAD_TOP + innerH + 14}
            fontSize="10"
            fill="#8e8e93"
            textAnchor="middle"
          >
            {p}%
          </text>
        ))}
        {/* Y tick labels */}
        {[0, 50, 100].map((p) => (
          <text
            key={`yt-${p}`}
            x={LZ_PAD_LEFT - 6}
            y={LZ_PAD_TOP + innerH - (p / 100) * innerH + 3}
            fontSize="10"
            fill="#8e8e93"
            textAnchor="end"
          >
            {p}%
          </text>
        ))}
      </svg>
      <p className="ov-lorenz-caption">
        {t("overview.concentration.lorenz_caption", {
          pct: share20Pct.toFixed(0),
        })}
      </p>
    </div>
  );
}
