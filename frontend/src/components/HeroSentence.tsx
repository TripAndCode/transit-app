// frontend/src/components/HeroSentence.tsx
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { OverviewHeadline } from "../api/types";

type Props = {
  headline: OverviewHeadline;
  sparkline_points: number[];
  /** "card" (default) uses the latest 7 days of ``sparkline_points``;
   *  "modal" uses the full series for a longer-form trend view. */
  variant?: "card" | "modal";
  /** When set, the hero is rendered as a button-like region. */
  onClick?: () => void;
};

type HoverState = {
  visible: boolean;
  // SVG-space coords for the in-SVG guide line / dot
  svgX: number;
  svgY: number;
  // Pixel coords (CSS) relative to the .ov-hero-spark wrapper for the HTML tooltip
  px: number;
  py: number;
  label: string;
  value: string;
};

// Full-width editorial sparkline. The viewBox uses preserveAspectRatio="none"
// so it stretches across the available content width — width here is just a
// reference unit for the coordinate system.
const SPARK_W = 820;
const SPARK_H = 96;
const PAD_TOP = 16;
const PAD_BOTTOM = 22;
const PAD_LEFT = 8;
const PAD_RIGHT = 56; // space for baseline label

function fmtNum(n: number | null | undefined): string {
  return n == null ? "—" : n.toFixed(1);
}


function storyKey(delta: number | null | undefined): string {
  if (delta == null) return "overview.story.no_baseline";
  if (delta < -0.05) return "overview.story.better";
  if (delta > 0.05) return "overview.story.worse";
  return "overview.story.same";
}

export function HeroSentence({
  headline,
  sparkline_points,
  variant = "card",
  onClick,
}: Props) {
  const { t } = useTranslation();
  const [hover, setHover] = useState<HoverState>({
    visible: false,
    svgX: 0,
    svgY: 0,
    px: 0,
    py: 0,
    label: "",
    value: "",
  });

  // Card variant only shows the last 7 days for a compact glance; modal
  // shows the full series. The eyebrow date label is anchored at the
  // start of whatever slice we ended up rendering.
  const pts =
    variant === "modal" ? sparkline_points : sparkline_points.slice(-7);
  const ptsStart = useMemo(() => {
    const dropped = Math.max(sparkline_points.length - pts.length, 0);
    const start = new Date(headline.window_from + "T00:00:00");
    // For the card view, window_from already matches the 7-day slice
    // (cur_ctx). For the modal view with a wider full series, shift the
    // labels back by the number of leading points that the modal adds.
    if (variant === "modal" && dropped === 0 && pts.length > 7) {
      start.setDate(start.getDate() - (pts.length - 7));
    }
    return start;
  }, [headline.window_from, pts.length, sparkline_points.length, variant]);

  const dateLabels = useMemo(() => {
    const out: string[] = [];
    for (let i = 0; i < pts.length; i++) {
      const d = new Date(ptsStart);
      d.setDate(ptsStart.getDate() + i);
      out.push(`${d.getMonth() + 1}/${d.getDate()}`);
    }
    return out;
  }, [ptsStart, pts.length]);

  const chartH = variant === "modal" ? 220 : SPARK_H;

  const chart = useMemo(() => {
    if (pts.length < 2) return null;
    const baseline = headline.baseline_avg_min;
    const dataMin = Math.min(...pts);
    const dataMax = Math.max(...pts);
    const yMin = baseline != null ? Math.min(dataMin, baseline) : dataMin;
    const yMax = baseline != null ? Math.max(dataMax, baseline) : dataMax;
    const span = (yMax - yMin) || 1;

    const usableW = SPARK_W - PAD_LEFT - PAD_RIGHT;
    const usableH = chartH - PAD_TOP - PAD_BOTTOM;
    const stepX = pts.length > 1 ? usableW / (pts.length - 1) : 0;
    const toX = (i: number) => PAD_LEFT + i * stepX;
    const toY = (v: number) =>
      PAD_TOP + (1 - (v - yMin) / span) * usableH;

    const coords = pts.map((v, i) => ({ x: toX(i), y: toY(v), v }));
    const line = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(" ");
    const area =
      `M ${coords[0].x.toFixed(1)},${chartH - PAD_BOTTOM} ` +
      coords
        .map((c) => `L ${c.x.toFixed(1)},${c.y.toFixed(1)}`)
        .join(" ") +
      ` L ${coords[coords.length - 1].x.toFixed(1)},${chartH - PAD_BOTTOM} Z`;

    const baselineY = baseline != null ? toY(baseline) : null;

    let minIdx = 0;
    let maxIdx = 0;
    pts.forEach((v, i) => {
      if (v < pts[minIdx]) minIdx = i;
      if (v > pts[maxIdx]) maxIdx = i;
    });
    const lastIdx = pts.length - 1;

    return { coords, line, area, baselineY, minIdx, maxIdx, lastIdx, stepX };
  }, [pts, headline.baseline_avg_min, chartH]);

  const isImprovement =
    headline.delta_min != null && headline.delta_min < 0;
  const deltaChipClass = isImprovement ? "ov-chip-down" : "ov-chip-up";

  function handleMove(e: React.MouseEvent<SVGSVGElement>) {
    if (!chart) return;
    const svg = e.currentTarget;
    const rect = svg.getBoundingClientRect();
    const scaleX = rect.width / SPARK_W;
    const scaleY = rect.height / chartH;
    const localX = (e.clientX - rect.left) / scaleX;
    let best = 0;
    let bestDist = Infinity;
    chart.coords.forEach((c, i) => {
      const d = Math.abs(c.x - localX);
      if (d < bestDist) {
        bestDist = d;
        best = i;
      }
    });
    const target = chart.coords[best];
    setHover({
      visible: true,
      svgX: target.x,
      svgY: target.y,
      px: target.x * scaleX,
      py: target.y * scaleY,
      label: dateLabels[best] ?? "",
      value: `${target.v.toFixed(1)}${t("overview.hero_unit_min")}`,
    });
  }

  function handleLeave() {
    setHover((h) => ({ ...h, visible: false }));
  }

  const story = t(storyKey(headline.delta_min));

  // Summary stats shown only in the modal variant.
  const summaryStats = useMemo(() => {
    if (variant !== "modal" || pts.length === 0) return null;
    const sum = pts.reduce((s, v) => s + v, 0);
    return {
      max: Math.max(...pts),
      min: Math.min(...pts),
      mean: sum / pts.length,
      count: pts.length,
    };
  }, [variant, pts]);

  const clickable = !!onClick;
  const sectionClass = `ov-hero${clickable ? " ov-clickable" : ""}`;
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

  return (
    <section className={sectionClass} {...interactiveProps}>
      <p className="ov-hero-eyebrow">
        {t("overview.eyebrow", {
          from: headline.window_from,
          to: headline.window_to,
        })}
      </p>
      {variant === "modal" && (
        <p className="ov-modal-explainer">
          {t("overview.hero_explainer", {
            from: headline.window_from,
            to: headline.window_to,
          })}
        </p>
      )}
      <h1 className="ov-hero-story">{story}</h1>
      <div className="ov-hero-row">
        <span className="ov-hero-number ov-anim-fade">
          {fmtNum(headline.avg_min)}
          {t("overview.hero_unit_min")}
        </span>
        <div className="ov-hero-meta">
          {headline.delta_min != null && (
            <span className={`ov-chip ${deltaChipClass}`}>
              {isImprovement ? "▼" : "▲"}{" "}
              {Math.abs(headline.delta_min).toFixed(1)}
              {t("overview.hero_unit_min")}
              {headline.delta_pct != null
                ? ` (${headline.delta_pct > 0 ? "+" : ""}${headline.delta_pct.toFixed(0)}%)`
                : ""}
            </span>
          )}
          {headline.delta_min == null && (
            <span className="ov-chip ov-chip-neutral">
              {t("overview.hero_no_baseline")}
            </span>
          )}
          <span className="sep" aria-hidden>·</span>
          <span className="ov-hero-samples">
            {t("overview.hero_samples", { count: headline.samples })}
          </span>
        </div>
      </div>

      <div className="ov-hero-spark">
        {chart && (
          <>
            <svg
              width="100%"
              height={chartH}
              viewBox={`0 0 ${SPARK_W} ${chartH}`}
              preserveAspectRatio="none"
              role="img"
              aria-label={t("overview.hero.label")}
              style={{ display: "block", overflow: "visible" }}
              onMouseMove={handleMove}
              onMouseLeave={handleLeave}
            >
              <defs>
                <linearGradient id="ovHeroFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" style={{ stopColor: "var(--trend-neutral)", stopOpacity: 0.10 }} />
                  <stop offset="100%" style={{ stopColor: "var(--trend-neutral)", stopOpacity: 0 }} />
                </linearGradient>
              </defs>
              <path d={chart.area} fill="url(#ovHeroFill)" stroke="none" />
              {chart.baselineY != null && (
                <>
                  <line
                    x1={PAD_LEFT}
                    y1={chart.baselineY}
                    x2={SPARK_W - PAD_RIGHT}
                    y2={chart.baselineY}
                    style={{ stroke: "var(--border-subtle)" }}
                    strokeWidth="1"
                    strokeDasharray="2 4"
                  />
                  <text
                    x={SPARK_W - PAD_RIGHT + 4}
                    y={chart.baselineY + 3}
                    fontSize="10"
                    style={{ fill: "var(--text-tertiary)" }}
                    textAnchor="start"
                  >
                    {t("overview.hero.baseline_label")}
                  </text>
                </>
              )}
              <polyline
                className="ov-spark-line"
                fill="none"
                style={{ stroke: "var(--trend-neutral)" }}
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                points={chart.line}
              />
              {/* min/max/last annotations */}
              {chart.coords.map((c, i) => {
                const isMin = i === chart.minIdx;
                const isMax = i === chart.maxIdx;
                const isLast = i === chart.lastIdx;
                if (!isMin && !isMax && !isLast) return null;
                return (
                  <g key={`ann-${i}`}>
                    {isLast ? (
                      <>
                        <circle
                          cx={c.x}
                          cy={c.y}
                          r="5"
                          style={{ fill: "var(--trend-neutral)", fillOpacity: 0.20 }}
                        />
                        <circle cx={c.x} cy={c.y} r="2.5" style={{ fill: "var(--trend-neutral)" }} />
                      </>
                    ) : (
                      <circle cx={c.x} cy={c.y} r="2" style={{ fill: "var(--text-tertiary)" }} />
                    )}
                    <text
                      x={c.x}
                      y={c.y - 8}
                      fontSize="10"
                      style={{ fill: "var(--text-secondary)" }}
                      textAnchor="middle"
                    >
                      {c.v.toFixed(1)}
                    </text>
                  </g>
                );
              })}
              {/* x-axis date labels under each dot. For dense (modal)
                  charts, thin them out so labels don't overlap. */}
              {chart.coords.map((c, i) => {
                if (variant === "modal" && pts.length > 10) {
                  const step = Math.ceil(pts.length / 10);
                  if (i % step !== 0 && i !== chart.lastIdx) return null;
                }
                return (
                  <text
                    key={`x-${i}`}
                    x={c.x}
                    y={chartH - 6}
                    fontSize="10"
                    style={{ fill: "var(--text-tertiary)" }}
                    textAnchor="middle"
                  >
                    {dateLabels[i]}
                  </text>
                );
              })}
              {/* hover guide */}
              {hover.visible && (
                <line
                  x1={hover.svgX}
                  y1={PAD_TOP - 4}
                  x2={hover.svgX}
                  y2={chartH - PAD_BOTTOM + 2}
                  style={{ stroke: "var(--trend-neutral)", strokeOpacity: 0.30 }}
                  strokeWidth="1"
                />
              )}
            </svg>
            {hover.visible && (
              <div
                className="ov-tooltip"
                style={{ left: hover.px, top: hover.py }}
              >
                {hover.label} — {hover.value}
              </div>
            )}
          </>
        )}
      </div>
      {summaryStats && (
        <div className="ov-stat-pills">
          <div className="ov-stat-pill">
            <span className="ov-stat-pill-label">
              {t("overview.hero.stat_max")}
            </span>
            <span className="ov-stat-pill-value">
              {summaryStats.max.toFixed(1)}
              {t("overview.hero_unit_min")}
            </span>
          </div>
          <div className="ov-stat-pill">
            <span className="ov-stat-pill-label">
              {t("overview.hero.stat_min")}
            </span>
            <span className="ov-stat-pill-value">
              {summaryStats.min.toFixed(1)}
              {t("overview.hero_unit_min")}
            </span>
          </div>
          <div className="ov-stat-pill">
            <span className="ov-stat-pill-label">
              {t("overview.hero.stat_mean")}
            </span>
            <span className="ov-stat-pill-value">
              {summaryStats.mean.toFixed(1)}
              {t("overview.hero_unit_min")}
            </span>
          </div>
          <div className="ov-stat-pill">
            <span className="ov-stat-pill-label">
              {t("overview.hero.stat_days")}
            </span>
            <span className="ov-stat-pill-value">{summaryStats.count}</span>
          </div>
        </div>
      )}
    </section>
  );
}
