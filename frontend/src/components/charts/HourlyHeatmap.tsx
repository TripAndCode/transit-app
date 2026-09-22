import { useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { useRangeContext, type TimeBand } from "../../api/rangeContext";
import { DELAY_THRESHOLDS, HEAT_RAMP, heatOpacity } from "../../styles/tokens";
import { useEnteredOnMount } from "../../hooks/useEnteredOnMount";
import { staggerDelay } from "./ChartEnter";
import { DIM_OPACITY, isFocusDimmed, isoDow, useTrendFocus } from "./trendFocus";

export type HourlyCell = {
  date: string;
  hour: number;
  avg_min: number | null;
  samples: number;
};

type Props = { cells: HourlyCell[]; height?: number };

// Hour ranges that map a clicked row to a time-band filter value.
const HOUR_TO_BAND: { hours: [number, number]; band: TimeBand }[] = [
  { hours: [0, 4], band: "late_night" },
  { hours: [5, 8], band: "morning" },
  { hours: [9, 11], band: "forenoon" },
  { hours: [12, 13], band: "noon" },
  { hours: [14, 16], band: "afternoon" },
  { hours: [17, 19], band: "evening" },
  { hours: [20, 23], band: "night" },
];

// Evenly spaced sample points across the ramp domain, for the legend strip.
// Derived from the ramp's own endpoint so retuning it moves the swatches and
// the `ramp_max` caption beside them together.
const RAMP_STOP_COUNT = 6;
const RAMP_STOPS = Array.from(
  { length: RAMP_STOP_COUNT },
  (_, i) => (HEAT_RAMP.maxMin * i) / (RAMP_STOP_COUNT - 1),
);

function bandFor(hour: number): TimeBand | null {
  for (const b of HOUR_TO_BAND) {
    if (hour >= b.hours[0] && hour <= b.hours[1]) return b.band;
  }
  return null;
}

/**
 * Date × hour-of-day heatmap. Rows = hours 0-23, columns = dates.
 *
 * Delay is one quantity, so it gets one hue (`--accent`) ramped light to dark
 * by opacity; a cell past the severe threshold is outlined rather than
 * recoloured, keeping the ordering of the ramp intact. Hover publishes the
 * cell's hour and weekday to `TrendFocusContext`, which dims the
 * non-matching marks in the sibling charts, and shows a tooltip with the
 * exact date/hour/avg/samples — the ramp answers "where", the tooltip
 * answers "how much".
 */
export function HourlyHeatmap({ cells, height = 280 }: Props) {
  const { t } = useTranslation();
  const [hover, setHover] = useState<HourlyCell | null>(null);
  const [showLegend, setShowLegend] = useState(false);
  const [, setCtx] = useRangeContext();
  const { focus, setFocus } = useTrendFocus();
  const entered = useEnteredOnMount();

  const dates = Array.from(new Set(cells.map((c) => c.date))).sort();

  const map = new Map<string, HourlyCell>();
  for (const c of cells) map.set(`${c.date}|${c.hour}`, c);

  if (dates.length === 0) {
    return (
      <div style={{ padding: 24, color: "var(--text-tertiary)", textAlign: "center" }}>
        {t("reports.heatmap.empty")}
      </div>
    );
  }

  const padL = 38;
  const padT = 12;
  const padB = 28;
  const innerH = height - padT - padB;
  const cellH = innerH / 24;
  const innerW = Math.max(360, dates.length * 14);
  const cellW = innerW / dates.length;

  return (
    <div style={{ position: "relative", width: "100%", marginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 13 }}>{t("reports.heatmap.title")}</strong>
        <span style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>
          {t("reports.heatmap.subtitle")}
        </span>
        <button
          type="button"
          onClick={() => setShowLegend((v) => !v)}
          aria-label={t("reports.heatmap.legend_aria")}
          style={{
            background: "transparent",
            border: "1px solid var(--border-subtle)",
            borderRadius: "50%",
            width: 18,
            height: 18,
            fontSize: "var(--text-xs)",
            color: "var(--text-secondary)",
            cursor: "pointer",
            padding: 0,
            lineHeight: 1,
          }}
        >
          ?
        </button>
        {showLegend && (
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              fontSize: "var(--text-xs)",
              color: "var(--text-secondary)",
              padding: "4px 10px",
              background: "var(--bg-soft)",
              borderRadius: 4,
            }}
          >
            <span>{t("reports.heatmap.delay_label")}</span>
            <span style={{ fontVariantNumeric: "tabular-nums" }}>0</span>
            <span style={{ display: "inline-flex", gap: 2 }} aria-hidden="true">
              {RAMP_STOPS.map((min) => (
                <span
                  key={min}
                  style={{
                    width: 14,
                    height: 10,
                    borderRadius: 2,
                    background: "var(--accent)",
                    opacity: heatOpacity(min),
                  }}
                />
              ))}
            </span>
            <span>{t("reports.heatmap.ramp_max", { min: HEAT_RAMP.maxMin })}</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
              <span
                style={{
                  width: 10,
                  height: 10,
                  borderRadius: 2,
                  border: "1px solid var(--delay-severe)",
                }}
              />
              {t("reports.heatmap.severe_outline", { min: DELAY_THRESHOLDS.severe })}
            </span>
            <span style={{ color: "var(--text-tertiary)", marginLeft: 4 }}>
              {t("reports.heatmap.legend_explainer")}
            </span>
          </div>
        )}
      </div>
      <div style={{ overflowX: "auto" }}>
      <svg width={padL + innerW + 8} height={height} role="img" aria-label={t("reports.heatmap.svg_aria")}>
        {Array.from({ length: 24 }, (_, h) => (
          <text
            key={`h-${h}`}
            x={padL - 6}
            y={padT + h * cellH + cellH / 2 + 4}
            fontSize="10"
            fill="var(--text-tertiary)"
            textAnchor="end"
            style={{ cursor: bandFor(h) ? "pointer" : "default" }}
            onClick={() => {
              const b = bandFor(h);
              if (b) setCtx({ time_band: b });
            }}
          >
            {h}
          </text>
        ))}
        {dates.map((d, i) => {
          const stride = Math.max(1, Math.floor(dates.length / 8));
          if (i % stride !== 0 && i !== dates.length - 1) return null;
          return (
            <text
              key={`d-${d}`}
              x={padL + i * cellW + cellW / 2}
              y={height - 8}
              fontSize="10"
              fill="var(--text-tertiary)"
              textAnchor="middle"
              style={{ cursor: "pointer" }}
              onClick={() => setCtx({ from: d, to: d })}
            >
              {d.slice(5)}
            </text>
          );
        })}
        {dates.flatMap((d, i) =>
          Array.from({ length: 24 }, (_, h) => {
            const c = map.get(`${d}|${h}`);
            const x = padL + i * cellW;
            const y = padT + h * cellH;
            const value = c?.avg_min ?? null;
            const fill = value != null ? "var(--accent)" : "var(--bg-soft)";
            const opacity = value != null ? heatOpacity(value) : 0.35;
            const dimmed = isFocusDimmed(focus, { date: d, hour: h, dow: isoDow(d) }, "hourly");
            const handleCellClick = () => {
              if (!c) return;
              const b = bandFor(c.hour);
              setCtx({ from: c.date, to: c.date, time_band: b ?? "all" });
            };
            // Two independent channels, deliberately: `opacity` carries the
            // magnitude ramp (and is what the staggered entrance fades to via
            // --cell-opacity), `fill-opacity` carries the crossfilter dim.
            // Sharing one channel would make a hover response inherit the
            // entrance transition's per-cell delay, which on a grid this size
            // is most of a second.
            const cell = (
              <rect
                key={`${d}|${h}`}
                data-testid="heat-cell"
                data-date={d}
                data-hour={h}
                x={x + 0.5}
                y={y + 0.5}
                width={Math.max(1, cellW - 1)}
                height={Math.max(1, cellH - 1)}
                opacity={opacity}
                fillOpacity={dimmed ? DIM_OPACITY : 1}
                className={`chart-cell-enter${entered ? " chart-cell-enter--in" : ""} chart-focus-dimmable`}
                style={
                  {
                    fill,
                    cursor: c ? "pointer" : "default",
                    "--cell-opacity": opacity,
                    ...staggerDelay(i * 24 + h),
                  } as CSSProperties
                }
                onMouseEnter={() => {
                  if (!c) return;
                  setHover(c);
                  setFocus({ source: "hourly", hour: c.hour, dow: isoDow(c.date) });
                }}
                onMouseLeave={() => {
                  setHover((v) => (v === c ? null : v));
                  setFocus(null);
                }}
                onClick={handleCellClick}
              />
            );
            if (value == null || value < DELAY_THRESHOLDS.severe) return [cell];
            // The threshold is an annotation drawn over the ramp, at full
            // opacity, so it survives however faint its cell happens to be.
            return [
              cell,
              <rect
                key={`severe-${d}|${h}`}
                data-testid="heat-severe-outline"
                pointerEvents="none"
                x={x + 0.5}
                y={y + 0.5}
                width={Math.max(1, cellW - 1)}
                height={Math.max(1, cellH - 1)}
                fill="none"
                stroke="var(--delay-severe)"
                strokeWidth="1"
                strokeOpacity={dimmed ? DIM_OPACITY : 0.9}
              />,
            ];
          }),
        )}
      </svg>
      </div>
      {hover && (
        <div
          style={{
            position: "absolute",
            top: 32,
            right: 8,
            background: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 4,
            padding: "6px 10px",
            fontSize: 12,
            boxShadow: "var(--el-2)",
            pointerEvents: "none",
          }}
        >
          {hover.date} {t("reports.heatmap.tooltip_hour", { hour: String(hover.hour).padStart(2, "0") })}
          {" "}
          {t("reports.heatmap.tooltip_metrics", { min: (hover.avg_min ?? 0).toFixed(2), count: hover.samples })}
        </div>
      )}
    </div>
  );
}
