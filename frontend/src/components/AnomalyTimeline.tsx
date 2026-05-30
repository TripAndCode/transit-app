// frontend/src/components/AnomalyTimeline.tsx
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { AnomaliesResponse } from "../api/types";
import { Skeleton } from "./Skeleton";

// ─── Public types ────────────────────────────────────────────────────────────

export type AnomalyTimelineProps = {
  data: AnomaliesResponse | undefined;
  isLoading: boolean;
  isError: boolean;
  /** Click an anomaly marker → caller can drill into that date. */
  onAnomalyClick?: (date: string, delta_sigma: number) => void;
};

// ─── Constants ────────────────────────────────────────────────────────────────

const CHART_HEIGHT = 100;   // SVG viewBox height
const PADDING = 10;          // padding on all sides in SVG units
const PLOT_H = CHART_HEIGHT - PADDING * 2; // 80px effective plot area
const SVG_HEIGHT_PX = 120;

const MIN_WIDTH = 280;
const MAX_WIDTH = 700;

// ─── Color helpers ────────────────────────────────────────────────────────────

/** Warm orange-tan for positive anomalies (above mean), calm green-teal for negative. Never red. */
function markerColor(delta_sigma: number): string {
  return delta_sigma > 0
    ? "hsl(25, 60%, 55%)"   // warm orange-tan
    : "hsl(160, 40%, 50%)"; // cool green-teal
}

// ─── Date formatting helpers ──────────────────────────────────────────────────

function fmtMMDD(dateStr: string): string {
  // dateStr: "YYYY-MM-DD"
  const parts = dateStr.split("-");
  if (parts.length < 3) return dateStr;
  return `${parts[1]}/${parts[2]}`;
}

// ─── Chart math ───────────────────────────────────────────────────────────────

function yScale(value: number, yMin: number, yMax: number): number {
  if (yMax === yMin) return PADDING + PLOT_H / 2;
  const norm = (value - yMin) / (yMax - yMin);
  // SVG y=0 is top; higher value → smaller y
  return PADDING + PLOT_H * (1 - norm);
}

function xScale(index: number, total: number, widthPx: number): number {
  if (total <= 1) return PADDING;
  return PADDING + ((index / (total - 1)) * (widthPx - PADDING * 2));
}

// ─── Component ───────────────────────────────────────────────────────────────

export function AnomalyTimeline({
  data,
  isLoading,
  isError,
  onAnomalyClick,
}: AnomalyTimelineProps): JSX.Element {
  const { t } = useTranslation();

  // ── Responsive width via ResizeObserver ──────────────────────────────────────
  const containerRef = useRef<HTMLDivElement>(null);
  const [widthPx, setWidthPx] = useState<number>(MIN_WIDTH);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const clampWidth = (w: number) =>
      Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, w));

    // Initial measurement
    setWidthPx(clampWidth(el.getBoundingClientRect().width));

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setWidthPx(clampWidth(entry.contentRect.width));
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // ── Loading ──────────────────────────────────────────────────────────────────
  if (isLoading) {
    return (
      <section aria-busy="true" aria-label={t("ask.dashboard.anomaly.title")}>
        <h3
          style={{
            margin: "0 0 4px",
            fontSize: 13,
            fontWeight: 600,
            color: "var(--text-primary, #333)",
          }}
        >
          {t("ask.dashboard.anomaly.title")}
        </h3>
        <Skeleton width="100%" height={SVG_HEIGHT_PX} />
      </section>
    );
  }

  // ── Error ────────────────────────────────────────────────────────────────────
  if (isError) {
    return (
      <section>
        {renderTitle()}
        <p
          style={{
            margin: "8px 0 0",
            fontSize: 13,
            color: "var(--text-secondary, #888)",
          }}
        >
          {t("ask.dashboard.anomaly.error")}
        </p>
      </section>
    );
  }

  // ── Empty state ───────────────────────────────────────────────────────────────
  if (!data || data.series.length === 0) {
    return (
      <section ref={containerRef}>
        {renderTitle()}
        <p
          style={{
            margin: "8px 0 0",
            fontSize: 13,
            color: "var(--text-secondary, #888)",
          }}
        >
          {t("ask.dashboard.anomaly.empty")}
        </p>
      </section>
    );
  }

  // ── Chart rendering ───────────────────────────────────────────────────────────
  const { series, mean, std, anomalies } = data;
  const n = series.length;

  // Compute y-axis domain — extend slightly beyond ±σ so band has visual breathing room
  const padding_y = std * 0.5;
  const yMin = mean - std - padding_y;
  const yMax = mean + std + padding_y;

  // Polyline points string
  const polyPoints = series
    .map((pt, i) => {
      const x = xScale(i, n, widthPx);
      const y = yScale(pt.avg_delay, yMin, yMax);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  // ±σ band y-coords
  const bandTop = yScale(mean + std, yMin, yMax);
  const bandBottom = yScale(mean - std, yMin, yMax);
  const bandHeight = bandBottom - bandTop;

  // Y-axis label positions
  const yLabelTop = yScale(mean + std, yMin, yMax);
  const yLabelMid = yScale(mean, yMin, yMax);
  const yLabelBot = yScale(mean - std, yMin, yMax);

  // X-axis date labels
  const firstDate = fmtMMDD(series[0].date);
  const lastDate = fmtMMDD(series[n - 1].date);
  const xFirst = xScale(0, n, widthPx);
  const xLast = xScale(n - 1, n, widthPx);

  return (
    <section ref={containerRef}>
      {renderTitle()}

      {/* Subtitle: mean ± std baseline */}
      <p
        style={{
          margin: "2px 0 6px",
          fontSize: 11,
          color: "var(--text-secondary, #888)",
          lineHeight: 1.3,
        }}
      >
        {t("ask.dashboard.anomaly.baseline", {
          mean: mean.toFixed(1),
          std: std.toFixed(1),
        })}
      </p>

      {/* SVG chart — width 100%, fixed pixel height */}
      <svg
        width="100%"
        height={SVG_HEIGHT_PX}
        viewBox={`0 0 ${widthPx} ${CHART_HEIGHT}`}
        aria-label={t("ask.dashboard.anomaly.title")}
        role="img"
        style={{ display: "block", overflow: "visible" }}
      >
        {/* ±σ background band */}
        <rect
          x={PADDING}
          y={bandTop}
          width={widthPx - PADDING * 2}
          height={Math.max(0, bandHeight)}
          fill="hsl(210, 30%, 92%)"
          stroke="none"
        />

        {/* Series line */}
        {n > 1 && (
          <polyline
            points={polyPoints}
            stroke="var(--accent, #4a8aaa)"
            strokeWidth={1.5}
            fill="none"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        )}

        {/* Anomaly markers */}
        {anomalies.map((marker) => {
          // Find the series point at this date
          const idx = series.findIndex((pt) => pt.date === marker.date);
          if (idx === -1) return null;
          const pt = series[idx];
          const cx = xScale(idx, n, widthPx);
          const cy = yScale(pt.avg_delay, yMin, yMax);
          const fill = markerColor(marker.delta_sigma);
          const sigmaSign = marker.delta_sigma >= 0 ? "+" : "";
          const ariaLabel = t("ask.dashboard.anomaly.marker_aria", {
            date: marker.date,
            avg: pt.avg_delay.toFixed(1),
            sigma: `${sigmaSign}${marker.delta_sigma.toFixed(1)}`,
          });

          return (
            <g
              key={marker.date}
              role="button"
              tabIndex={0}
              aria-label={ariaLabel}
              style={{ cursor: "pointer" }}
              onClick={() => onAnomalyClick?.(marker.date, marker.delta_sigma)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onAnomalyClick?.(marker.date, marker.delta_sigma);
                }
              }}
            >
              <circle
                cx={cx}
                cy={cy}
                r={3.5}
                fill={fill}
                stroke="white"
                strokeWidth={1}
                style={{
                  transition: "r 0.12s ease",
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as SVGCircleElement).setAttribute("r", "5");
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as SVGCircleElement).setAttribute("r", "3.5");
                }}
              />
            </g>
          );
        })}

        {/* Y-axis labels — right-aligned at left edge */}
        <text
          x={PADDING - 3}
          y={yLabelTop}
          fontSize={10}
          fill="var(--text-secondary, #888)"
          textAnchor="end"
          dominantBaseline="middle"
        >
          {(mean + std).toFixed(1)}
        </text>
        <text
          x={PADDING - 3}
          y={yLabelMid}
          fontSize={10}
          fill="var(--text-secondary, #888)"
          textAnchor="end"
          dominantBaseline="middle"
        >
          {mean.toFixed(1)}
        </text>
        <text
          x={PADDING - 3}
          y={yLabelBot}
          fontSize={10}
          fill="var(--text-secondary, #888)"
          textAnchor="end"
          dominantBaseline="middle"
        >
          {(mean - std).toFixed(1)}
        </text>

        {/* X-axis date labels — first (left-anchored) + last (right-anchored) */}
        <text
          x={xFirst}
          y={CHART_HEIGHT - 1}
          fontSize={10}
          fill="var(--text-secondary, #888)"
          textAnchor="start"
          dominantBaseline="auto"
        >
          {firstDate}
        </text>
        {n > 1 && (
          <text
            x={xLast}
            y={CHART_HEIGHT - 1}
            fontSize={10}
            fill="var(--text-secondary, #888)"
            textAnchor="end"
            dominantBaseline="auto"
          >
            {lastDate}
          </text>
        )}
      </svg>
    </section>
  );

  // ── Title renderer ────────────────────────────────────────────────────────────
  function renderTitle() {
    return (
      <h3
        style={{
          margin: "0 0 2px",
          fontSize: 13,
          fontWeight: 600,
          color: "var(--text-primary, #333)",
        }}
      >
        {t("ask.dashboard.anomaly.title")}
      </h3>
    );
  }
}
