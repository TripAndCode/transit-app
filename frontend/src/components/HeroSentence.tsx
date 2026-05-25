// frontend/src/components/HeroSentence.tsx
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { OverviewHeadline } from "../api/types";

type Props = {
  headline: OverviewHeadline;
  sparkline_points: number[];
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

function buildDateLabels(windowFrom: string, count: number): string[] {
  const start = new Date(windowFrom + "T00:00:00");
  const out: string[] = [];
  for (let i = 0; i < count; i++) {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    out.push(`${d.getMonth() + 1}/${d.getDate()}`);
  }
  return out;
}

function storyKey(delta: number | null | undefined): string {
  if (delta == null) return "overview.story.no_baseline";
  if (delta < -0.05) return "overview.story.better";
  if (delta > 0.05) return "overview.story.worse";
  return "overview.story.same";
}

export function HeroSentence({ headline, sparkline_points }: Props) {
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

  const dateLabels = useMemo(
    () => buildDateLabels(headline.window_from, sparkline_points.length),
    [headline.window_from, sparkline_points.length],
  );

  const chart = useMemo(() => {
    const pts = sparkline_points;
    if (pts.length < 2) return null;
    const baseline = headline.baseline_avg_min;
    const dataMin = Math.min(...pts);
    const dataMax = Math.max(...pts);
    const yMin = baseline != null ? Math.min(dataMin, baseline) : dataMin;
    const yMax = baseline != null ? Math.max(dataMax, baseline) : dataMax;
    const span = (yMax - yMin) || 1;

    const usableW = SPARK_W - PAD_LEFT - PAD_RIGHT;
    const usableH = SPARK_H - PAD_TOP - PAD_BOTTOM;
    const stepX = pts.length > 1 ? usableW / (pts.length - 1) : 0;
    const toX = (i: number) => PAD_LEFT + i * stepX;
    const toY = (v: number) =>
      PAD_TOP + (1 - (v - yMin) / span) * usableH;

    const coords = pts.map((v, i) => ({ x: toX(i), y: toY(v), v }));
    const line = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(" ");
    const area =
      `M ${coords[0].x.toFixed(1)},${SPARK_H - PAD_BOTTOM} ` +
      coords
        .map((c) => `L ${c.x.toFixed(1)},${c.y.toFixed(1)}`)
        .join(" ") +
      ` L ${coords[coords.length - 1].x.toFixed(1)},${SPARK_H - PAD_BOTTOM} Z`;

    const baselineY = baseline != null ? toY(baseline) : null;

    let minIdx = 0;
    let maxIdx = 0;
    pts.forEach((v, i) => {
      if (v < pts[minIdx]) minIdx = i;
      if (v > pts[maxIdx]) maxIdx = i;
    });
    const lastIdx = pts.length - 1;

    return { coords, line, area, baselineY, minIdx, maxIdx, lastIdx, stepX };
  }, [sparkline_points, headline.baseline_avg_min]);

  const isImprovement =
    headline.delta_min != null && headline.delta_min < 0;
  const deltaChipClass = isImprovement ? "ov-chip-down" : "ov-chip-up";

  function handleMove(e: React.MouseEvent<SVGSVGElement>) {
    if (!chart) return;
    const svg = e.currentTarget;
    const rect = svg.getBoundingClientRect();
    const scaleX = rect.width / SPARK_W;
    const scaleY = rect.height / SPARK_H;
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

  return (
    <section className="ov-hero">
      <p className="ov-hero-eyebrow">
        {t("overview.eyebrow", {
          from: headline.window_from,
          to: headline.window_to,
        })}
      </p>
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
              height={SPARK_H}
              viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
              preserveAspectRatio="none"
              role="img"
              aria-label={t("overview.hero.label")}
              style={{ display: "block", overflow: "visible" }}
              onMouseMove={handleMove}
              onMouseLeave={handleLeave}
            >
              <defs>
                <linearGradient id="ovHeroFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="rgba(71,85,105,0.10)" />
                  <stop offset="100%" stopColor="rgba(71,85,105,0)" />
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
                    stroke="#cbd5e1"
                    strokeWidth="1"
                    strokeDasharray="2 4"
                  />
                  <text
                    x={SPARK_W - PAD_RIGHT + 4}
                    y={chart.baselineY + 3}
                    fontSize="10"
                    fill="#94a3b8"
                    textAnchor="start"
                  >
                    {t("overview.hero.baseline_label")}
                  </text>
                </>
              )}
              <polyline
                className="ov-spark-line"
                fill="none"
                stroke="#475569"
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
                          fill="rgba(71,85,105,0.20)"
                        />
                        <circle cx={c.x} cy={c.y} r="2.5" fill="#475569" />
                      </>
                    ) : (
                      <circle cx={c.x} cy={c.y} r="2" fill="#94a3b8" />
                    )}
                    <text
                      x={c.x}
                      y={c.y - 8}
                      fontSize="10"
                      fill="#6e6e73"
                      textAnchor="middle"
                    >
                      {c.v.toFixed(1)}
                    </text>
                  </g>
                );
              })}
              {/* x-axis date labels under each dot */}
              {chart.coords.map((c, i) => (
                <text
                  key={`x-${i}`}
                  x={c.x}
                  y={SPARK_H - 6}
                  fontSize="10"
                  fill="#94a3b8"
                  textAnchor="middle"
                >
                  {dateLabels[i]}
                </text>
              ))}
              {/* hover guide */}
              {hover.visible && (
                <line
                  x1={hover.svgX}
                  y1={PAD_TOP - 4}
                  x2={hover.svgX}
                  y2={SPARK_H - PAD_BOTTOM + 2}
                  stroke="rgba(71,85,105,0.30)"
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
    </section>
  );
}
