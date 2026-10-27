import { useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { useScope } from "../../api/scope";
import { bandOf } from "../../api/timeBands";
import { DELAY_THRESHOLDS, HEAT_RAMP, heatOpacity } from "../../styles/tokens";
import { useFirstData } from "../../hooks/useFirstData";
import { useRovingCells } from "../../hooks/useRovingCells";
import { staggerDelay } from "./ChartEnter";
import { isoDow, useTrendFocus } from "./trendFocus";
import { onActivateKey } from "../../utils/a11y";
import { formatDate, formatMinutes, formatNumber, formatShortDate } from "../../utils/format";

export type HourlyCell = {
  date: string;
  hour: number;
  avg_min: number | null;
  samples: number;
};

type Props = { cells: HourlyCell[]; height?: number };

/** The hour and date labels' size, which is also each hour row's minimum height. */
const HOUR_LABEL_PX = 12;

// Evenly spaced sample points across the ramp domain, for the legend strip.
// Derived from the ramp's own endpoint so retuning it moves the swatches and
// the `ramp_max` caption beside them together.
const RAMP_STOP_COUNT = 6;
const RAMP_STOPS = Array.from(
  { length: RAMP_STOP_COUNT },
  (_, i) => (HEAT_RAMP.maxMin * i) / (RAMP_STOP_COUNT - 1),
);

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
  const [, setCtx] = useScope();
  const { setFocus } = useTrendFocus();
  const entered = useFirstData(cells.length > 0);

  const dates = Array.from(new Set(cells.map((c) => c.date))).sort();

  const map = new Map<string, HourlyCell>();
  for (const c of cells) map.set(`${c.date}|${c.hour}`, c);

  // Every cell, empty ones included, is a slot: a keyboard or screen-reader
  // user reads what the tooltip shows a pointer, with one tab stop for the
  // grid. Row-major by hour, matching the rows the grid exposes.
  const slots = Array.from({ length: 24 * dates.length }, (_, k) => `${dates[k % dates.length]}|${Math.floor(k / dates.length)}`);
  const { containerRef, activeSlot, onKeyDown, onCellFocus } = useRovingCells<SVGGElement>(slots, dates.length);
  // Keyed by date and hour, not by slot index: a redraw with other dates
  // moves every index, and the ring must stay on the cell that has focus.
  const [focusedKey, setFocusedKey] = useState<string | null>(null);

  if (dates.length === 0) {
    return (
      <div style={{ padding: 24, color: "var(--text-tertiary)", textAlign: "center" }}>
        {t("reports.heatmap.empty")}
      </div>
    );
  }

  // A clicked hour narrows the screen to the time band it falls in.
  function filterHour(hour: number) {
    setCtx({ time_band: bandOf(hour) });
  }
  function filterDay(date: string) {
    setCtx({ from: date, to: date });
  }

  const padL = 38;
  const padT = 12;
  const padB = 28;
  // Each hour's row is at least as tall as its label, so the 24 never overlap.
  const cellH = Math.max(HOUR_LABEL_PX, (height - padT - padB) / 24);
  const chartHeight = padT + padB + 24 * cellH;
  const innerW = Math.max(360, dates.length * 14);
  const cellW = innerW / dates.length;
  const dateLabels = dates.map((d) => formatDate(d));
  const severe = cells.filter((c) => c.avg_min != null && c.avg_min >= DELAY_THRESHOLDS.severe);
  const focusedCol = focusedKey == null ? -1 : dates.indexOf(focusedKey.split("|")[0]);
  const focusedRow = focusedKey == null ? -1 : Number(focusedKey.split("|")[1]);

  return (
    <div data-focus-viewer="hourly" style={{ position: "relative", width: "100%", marginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 13 }}>{t("reports.heatmap.title")}</strong>
        <span style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>
          {t("reports.heatmap.subtitle")}
        </span>
        <button
          type="button"
          onClick={() => setShowLegend((v) => !v)}
          aria-label={t("reports.heatmap.legend_aria")}
          aria-expanded={showLegend}
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
      <svg width={padL + innerW + 8} height={chartHeight} role="group" aria-label={t("reports.heatmap.svg_aria")}>
        {Array.from({ length: 24 }, (_, h) => (
          <text
            key={`h-${h}`}
            x={padL - 6}
            y={padT + h * cellH + cellH / 2 + 4}
            fontSize={HOUR_LABEL_PX}
            fill="var(--text-tertiary)"
            textAnchor="end"
            style={{ cursor: "pointer" }}
            role="button"
            tabIndex={0}
            aria-label={t("reports.heatmap.hour_filter_aria", { hour: h })}
            onClick={() => filterHour(h)}
            onKeyDown={onActivateKey(() => filterHour(h))}
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
              y={chartHeight - 8}
              fontSize={HOUR_LABEL_PX}
              fill="var(--text-tertiary)"
              textAnchor="middle"
              style={{ cursor: "pointer" }}
              role="button"
              tabIndex={0}
              aria-label={t("reports.heatmap.date_filter_aria", { date: formatDate(d) })}
              onClick={() => filterDay(d)}
              onKeyDown={onActivateKey(() => filterDay(d))}
            >
              {formatShortDate(d)}
            </text>
          );
        })}
        <g
          ref={containerRef}
          role="grid"
          aria-label={t("reports.heatmap.title")}
          tabIndex={-1}
          aria-rowcount={24}
          aria-colcount={dates.length}
          onKeyDown={onKeyDown}
        >
          {Array.from({ length: 24 }, (_, h) => {
            const hourText = String(h).padStart(2, "0");
            const hourLabel = t("reports.heatmap.tooltip_hour", { hour: hourText });
            return (
            <g key={`row-${h}`} role="row">
              {dates.map((d, i) => {
                const c = map.get(`${d}|${h}`);
                const x = padL + i * cellW;
                const y = padT + h * cellH;
                const value = c?.avg_min ?? null;
                const fill = value != null ? "var(--accent)" : "var(--bg-soft)";
                const opacity = value != null ? heatOpacity(value) : 0.35;
                const index = h * dates.length + i;
                // One "has data" gate for the name and the tooltip, so a cell
                // read out as having no data never shows a 0-minute tooltip.
                const shown = c && value != null ? c : null;
                const label = shown
                  ? `${dateLabels[i]} ${hourLabel} ${t("reports.heatmap.tooltip_metrics", { min: formatMinutes(value as number), count: formatNumber(shown.samples) })}`
                  : t("reports.heatmap.cell_empty_aria", { date: dateLabels[i], hour: hourText });
                const show = () => {
                  if (!shown) return;
                  setHover(shown);
                  setFocus({ source: "hourly", hour: shown.hour, dow: isoDow(shown.date) });
                };
                const hide = () => {
                  setHover((v) => (v && v.date === d && v.hour === h ? null : v));
                  setFocus(null);
                };
                const handleCellClick = () => {
                  if (!c) return;
                  setCtx({ from: c.date, to: c.date, time_band: bandOf(c.hour) });
                };
                // Two independent channels, deliberately: `opacity` carries the
                // magnitude ramp (and is what the staggered entrance fades to via
                // --cell-opacity), `fill-opacity` carries the crossfilter dim,
                // which `.focus-dim-fill` reads from --focus-dim. The dim is not
                // transitioned: the inline per-cell delay would apply to it too,
                // and on a grid this size that is most of a second.
                return (
                  <rect
                    key={d}
                    data-testid="heat-cell"
                    data-date={d}
                    data-hour={h}
                    data-mark-date={d}
                    data-mark-dow={isoDow(d)}
                    data-cell={index}
                    role="gridcell"
                    aria-label={label}
                    tabIndex={activeSlot === `${d}|${h}` ? 0 : -1}
                    x={x + 0.5}
                    y={y + 0.5}
                    width={Math.max(1, cellW - 1)}
                    height={Math.max(1, cellH - 1)}
                    opacity={opacity}
                    className={`chart-cell-enter${entered ? " chart-cell-enter--in" : ""} chart-focus-dimmable focus-dim-fill`}
                    style={
                      {
                        fill,
                        cursor: c ? "pointer" : "default",
                        "--cell-opacity": opacity,
                        ...staggerDelay(i * 24 + h),
                      } as CSSProperties
                    }
                    onMouseEnter={show}
                    onMouseLeave={hide}
                    onFocus={() => {
                      onCellFocus(index);
                      setFocusedKey(`${d}|${h}`);
                      show();
                    }}
                    onBlur={() => {
                      setFocusedKey(null);
                      hide();
                    }}
                    onClick={handleCellClick}
                    onKeyDown={onActivateKey(handleCellClick)}
                  />
                );
              })}
            </g>
            );
          })}
        </g>
        {/* Annotations drawn over the ramp at full opacity, so they survive
            however faint their cell happens to be. */}
        <g aria-hidden="true">
          {severe.map((c) => (
            <rect
              key={`severe-${c.date}|${c.hour}`}
              data-testid="heat-severe-outline"
              data-mark-date={c.date}
              data-mark-dow={isoDow(c.date)}
              className="chart-focus-dimmable focus-dim-stroke"
              pointerEvents="none"
              x={padL + dates.indexOf(c.date) * cellW + 0.5}
              y={padT + c.hour * cellH + 0.5}
              width={Math.max(1, cellW - 1)}
              height={Math.max(1, cellH - 1)}
              fill="none"
              stroke="var(--delay-severe)"
              strokeWidth="1"
            />
          ))}
          {focusedCol >= 0 && (
            <rect
              data-testid="heat-focus-ring"
              pointerEvents="none"
              x={padL + focusedCol * cellW}
              y={padT + focusedRow * cellH}
              width={cellW}
              height={cellH}
              fill="none"
              stroke="var(--accent)"
              strokeWidth="2"
            />
          )}
        </g>
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
          {formatDate(hover.date)} {t("reports.heatmap.tooltip_hour", { hour: String(hover.hour).padStart(2, "0") })}
          {" "}
          {t("reports.heatmap.tooltip_metrics", { min: formatMinutes(hover.avg_min ?? 0), count: formatNumber(hover.samples) })}
        </div>
      )}
    </div>
  );
}
