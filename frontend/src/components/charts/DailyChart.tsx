import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { DELAY_THRESHOLDS, delayColor } from "../../styles/tokens";
import { formatNumber } from "../../utils/format";
import { useRangeContext } from "../../api/rangeContext";
import { useDrawOn } from "./ChartEnter";
import { ShadedDays, ThresholdBand, VerticalMarker } from "./annotations";
import { brushIndices, brushRange } from "./brush";
import { DIM_OPACITY, isFocusDimmed, isoDow, useTrendFocus } from "./trendFocus";
import type { RevisionBoundaries, TrendDay } from "../../api/types";

type Props = {
  days: TrendDay[];
  height?: number;
  revisionBoundaries?: RevisionBoundaries;
  /** Whether dragging across the plot rewrites the shared date range. Off for
   *  embeds (an Ask answer card) where the chart illustrates one reply rather
   *  than driving the page's filters. */
  brushable?: boolean;
};

/** An in-progress selection: the day the drag started on and the day the
 *  pointer (or keyboard cursor) is on now. Either may be the larger index. */
type Drag = { anchor: number; head: number };

export function DailyChart({ days, height = 240, revisionBoundaries = [], brushable = true }: Props) {
  const { t } = useTranslation();
  const [rawHover, setHover] = useState<number | null>(null);
  const [rawDrag, setDrag] = useState<Drag | null>(null);
  const [brushed, setBrushed] = useState(false);
  const [, updateRange] = useRangeContext();
  const { focus, setFocus } = useTrendFocus();
  const lineRef = useRef<SVGPolylineElement | null>(null);
  useDrawOn(lineRef);

  // If the data shrinks (filter narrowed), a stale hover index would
  // dereference out-of-bounds — clamp during render instead of an effect.
  const hover = rawHover != null && rawHover < days.length ? rawHover : null;
  // A selection carries two indices and has the same hazard, but it is
  // dropped rather than clamped: a range quietly resized to span different
  // days than the user drew is worse than no range at all.
  const drag =
    rawDrag != null && rawDrag.anchor < days.length && rawDrag.head < days.length ? rawDrag : null;
  const W = 760;
  const H = height;
  const padL = 44;
  const padR = 12;
  const padT = 12;
  const padB = 28;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  const avgs = days.map((d) => d.avg_min ?? 0);
  const samples = days.map((d) => d.samples ?? 0);
  const stats = {
    maxAvg: Math.max(1, ...avgs),
    maxSamples: Math.max(1, ...samples),
  };

  function focusDay(i: number) {
    setHover(i);
    const date = days[i]?.date;
    if (date) setFocus({ source: "daily", date, dow: isoDow(date) });
  }

  function clearDay(i: number) {
    setHover((v) => (v === i ? null : v));
    setFocus(null);
  }

  /** Apply the current selection to the shared range, or drop it if the
   *  pointer never moved off the day it went down on. */
  function commitBrush() {
    if (!drag) return;
    const selection = drag.anchor === drag.head ? null : brushRange(drag.anchor, drag.head, days);
    setDrag(null);
    if (!selection) return;
    updateRange({ from: selection.from, to: selection.to });
    // The chart is about to re-render over exactly the brushed range, so the
    // shading has nothing left to mark; the reset chip is what stays behind
    // to say the range is no longer the one the rest of the tab defaulted to.
    setBrushed(true);
  }

  function resetBrush() {
    setDrag(null);
    setBrushed(false);
    updateRange({ from: null, to: null });
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (days.length === 0) return;
    if (e.key === "Escape") {
      setDrag(null);
      return;
    }
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      commitBrush();
      return;
    }
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const current = drag ? drag.head : (hover ?? 0);
    const next = Math.min(days.length - 1, Math.max(0, current + (e.key === "ArrowRight" ? 1 : -1)));
    // Shift extends from wherever the selection was anchored; a bare arrow
    // moves the read-out cursor and abandons any selection in progress.
    setDrag(e.shiftKey ? { anchor: drag ? drag.anchor : current, head: next } : null);
    focusDay(next);
  }

  if (!days.length) {
    return (
      <div style={{ padding: 24, color: "var(--text-tertiary)", textAlign: "center" }}>
        {t("reports.daily.empty")}
      </div>
    );
  }

  const stepX = innerW / Math.max(1, days.length - 1);
  // Value → y, clamped to the plotted band so an annotation can be asked for
  // a threshold sitting above everything in range without escaping the plot.
  const toY = (value: number) =>
    padT + innerH - (Math.min(Math.max(value, 0), stats.maxAvg) / stats.maxAvg) * innerH * 0.65;
  const bandX = (i: number) => padL + i * stepX - stepX / 2;
  const linePts = days.map((d, i) => [padL + i * stepX, toY(d.avg_min ?? 0)] as [number, number]);
  // Trailing 7-day average sits ALONGSIDE the raw line (not a replacement) —
  // a low-traffic day's noisy raw figure is easier to read against a smooth
  // reference. Absent entirely for week/month-bucketed series and for
  // cached responses from before this field existed, so only draw it when
  // at least one day actually has a value; missing individual days (rare —
  // see compute_trend_series's docstring) are simply skipped rather than
  // breaking the line at 0.
  const smoothedPts = days
    .map((d, i) =>
      d.avg_min_smoothed != null ? ([padL + i * stepX, toY(d.avg_min_smoothed)] as [number, number]) : null,
    )
    .filter((p): p is [number, number] => p !== null);
  const hasSmoothed = smoothedPts.length > 0;

  // Schedule-revision boundary markers — a date within `days` where the
  // static feed version changed from the previous calendar day. A metric
  // shift at that x position reads as "a timetable revision happened here",
  // not a service-quality change. A boundary date absent from `days`
  // (shouldn't happen — the backend derives both from the same range — but a
  // stale cached response could disagree) is silently skipped rather than
  // crashing on a -1 index.
  const boundaryIndices = revisionBoundaries
    .map((bd) => days.findIndex((d) => d.date === bd))
    .filter((i) => i >= 0);

  const worstIdx = days.reduce(
    (best, d, i) => ((d.avg_min ?? 0) > (days[best].avg_min ?? 0) ? i : best),
    0,
  );
  const showWorst = days.length > 1 && (days[worstIdx].avg_min ?? 0) > 0;
  const selected = drag ? brushIndices(drag.anchor, drag.head) : [];
  const cursor = drag ? drag.head : hover;

  return (
    <div style={{ position: "relative", width: "100%" }}>
      {brushable && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            minHeight: 24,
            marginBottom: 4,
            flexWrap: "wrap",
          }}
        >
          <span style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>
            {t("reports.daily.brush_hint")}
          </span>
          {brushed && (
            <button
              type="button"
              onClick={resetBrush}
              style={{
                minHeight: 24,
                padding: "2px 12px",
                borderRadius: 999,
                border: "1px solid var(--border-subtle)",
                background: "var(--bg-soft)",
                color: "var(--text-secondary)",
                fontSize: "var(--text-xs)",
                cursor: "pointer",
              }}
            >
              {t("reports.daily.brush_reset")}
            </button>
          )}
        </div>
      )}
      <div
        {...(brushable
          ? {
              className: "chart-brush-surface",
              role: "slider",
              tabIndex: 0,
              "aria-label": t("reports.daily.brush_aria"),
              "aria-valuemin": 0,
              "aria-valuemax": days.length - 1,
              "aria-valuenow": cursor ?? 0,
              "aria-valuetext": drag
                ? t("reports.daily.brush_range", {
                    from: days[Math.min(drag.anchor, drag.head)].date,
                    to: days[Math.max(drag.anchor, drag.head)].date,
                  })
                : (days[cursor ?? 0]?.date ?? ""),
              onKeyDown,
              onMouseUp: commitBrush,
              // A release outside the plot still has to resolve the drag;
              // ending it at the last day the pointer was over beats leaving
              // the chart stuck in a selection the user can no longer see
              // themselves making.
              onMouseLeave: commitBrush,
            }
          : {})}
        style={{ overflowX: "auto" }}
      >
        <svg width={W} height={H} role="img" aria-label={t("reports.daily.svg_aria")} style={{ display: "block" }}>
          {/* Y axis grid */}
          {[0.25, 0.5, 0.75].map((f) => {
            const y = padT + innerH * 0.35 + (1 - f) * innerH * 0.65;
            return (
              <g key={f}>
                <line x1={padL} x2={W - padR} y1={y} y2={y} stroke="var(--border-soft)" strokeDasharray="2 4" />
                <text x={6} y={y + 4} fontSize="10" fill="var(--text-tertiary)">
                  {(stats.maxAvg * f).toFixed(1)}m
                </text>
              </g>
            );
          })}
          {/* Annotations sit under the data so they read as background
              context, not a second series. */}
          <ThresholdBand
            min={DELAY_THRESHOLDS.severe}
            max={stats.maxAvg}
            toY={toY}
            x={padL}
            width={innerW}
            label={t("reports.daily.severe_band_label", { min: DELAY_THRESHOLDS.severe })}
          />
          <ShadedDays days={selected} toX={bandX} bandWidth={stepX} y={padT} height={innerH} />
          {/* Sample-count bars (lower 30% of chart) */}
          {days.map((d, i) => {
            const x = padL + i * stepX - Math.max(1, stepX * 0.4);
            const w = Math.max(2, stepX * 0.8);
            const h = ((d.samples ?? 0) / stats.maxSamples) * innerH * 0.3;
            const y = padT + innerH - h;
            const dimmed = isFocusDimmed(focus, { date: d.date, dow: isoDow(d.date) }, "daily");
            return (
              <rect
                key={`bar-${i}`}
                data-testid="daily-bar"
                data-index={i}
                className="chart-focus-dimmable"
                x={x}
                y={y}
                width={w}
                height={h}
                fill="var(--accent-soft)"
                opacity={dimmed ? DIM_OPACITY : 0.7}
              />
            );
          })}
          {boundaryIndices.map((i) => (
            <VerticalMarker
              key={`rev-${i}`}
              x={padL + i * stepX}
              y1={padT}
              y2={padT + innerH}
              label={t("reports.daily.revision_boundary_label")}
              title={t("reports.daily.revision_boundary_aria", { date: days[i].date })}
            />
          ))}
          {showWorst && (
            <VerticalMarker
              x={padL + worstIdx * stepX}
              y1={padT}
              y2={padT + innerH}
              variant="highlight"
              label={t("reports.daily.worst_day_label")}
              title={days[worstIdx].date}
            />
          )}
          {/* Trailing 7-day average — drawn under the raw line so the raw
              line + dots stay the primary, foreground signal. */}
          {hasSmoothed && (
            <polyline
              points={smoothedPts.map((p) => p.join(",")).join(" ")}
              fill="none"
              stroke="var(--text-secondary)"
              strokeWidth="1.5"
              strokeDasharray="4 3"
              opacity={0.8}
            />
          )}
          {/* Line */}
          <polyline
            ref={lineRef}
            points={linePts.map((p) => p.join(",")).join(" ")}
            fill="none"
            stroke="var(--accent)"
            strokeWidth="2"
          />
          {/* Points + hover/drag targets */}
          {days.map((d, i) => {
            const [x, y] = linePts[i];
            const c = delayColor(d.avg_min ?? 0);
            const dimmed = isFocusDimmed(focus, { date: d.date, dow: isoDow(d.date) }, "daily");
            return (
              <g key={`pt-${i}`}>
                {/* `fill` goes in `style`, not the SVG presentation attribute:
                    delayColor()'s severe tier is now the literal "var(--delay-severe)",
                    and var() only resolves in a CSS property, not a presentation attr. */}
                <circle
                  data-testid="daily-dot"
                  data-index={i}
                  className="chart-focus-dimmable"
                  cx={x}
                  cy={y}
                  r={cursor === i ? 5 : 3}
                  opacity={dimmed ? DIM_OPACITY : 1}
                  style={{ fill: c, stroke: "var(--bg-surface)" }}
                  strokeWidth="1.5"
                />
                <rect
                  data-testid="daily-day"
                  data-index={i}
                  x={bandX(i)}
                  y={padT}
                  width={Math.max(1, stepX)}
                  height={innerH}
                  fill="transparent"
                  onMouseDown={brushable ? () => setDrag({ anchor: i, head: i }) : undefined}
                  onMouseEnter={() => {
                    focusDay(i);
                    setDrag((d2) => (d2 ? { ...d2, head: i } : d2));
                  }}
                  onMouseLeave={() => clearDay(i)}
                />
              </g>
            );
          })}
          {/* X axis tick labels (every Nth day) */}
          {days.map((d, i) => {
            const stride = Math.max(1, Math.floor(days.length / 8));
            if (i % stride !== 0 && i !== days.length - 1) return null;
            const x = padL + i * stepX;
            return (
              <text
                key={`xt-${i}`}
                x={x}
                y={H - 8}
                fontSize="10"
                fill="var(--text-tertiary)"
                textAnchor="middle"
              >
                {d.date.slice(5)}
              </text>
            );
          })}
        </svg>
      </div>
      {hasSmoothed && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: "var(--text-xs)",
            color: "var(--text-tertiary)",
            marginTop: 4,
          }}
        >
          <svg width="16" height="2" aria-hidden="true">
            <line x1="0" y1="1" x2="16" y2="1" stroke="var(--text-secondary)" strokeWidth="1.5" strokeDasharray="4 3" />
          </svg>
          <span>{t("reports.daily.smoothed_label")}</span>
        </div>
      )}
      {cursor != null && cursor < days.length && (
        <div
          style={{
            position: "absolute",
            top: 32,
            left: 60,
            background: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 4,
            padding: "6px 10px",
            fontSize: 12,
            boxShadow: "var(--el-2)",
            pointerEvents: "none",
          }}
        >
          <div>
            <strong>{days[cursor].date}</strong>:{" "}
            {t("reports.daily.tooltip_metrics", {
              min: (days[cursor].avg_min ?? 0).toFixed(2),
              count: formatNumber(days[cursor].samples ?? 0),
            })}
          </div>
          {days[cursor].avg_min_smoothed != null && (
            <div style={{ color: "var(--text-secondary)" }}>
              {t("reports.daily.smoothed_tooltip", { min: days[cursor].avg_min_smoothed!.toFixed(2) })}
            </div>
          )}
          {days[cursor].top_offenders?.length > 0 && (
            <div style={{ marginTop: 4, color: "var(--text-secondary)" }}>
              {t("reports.daily.worst_label")}{" "}
              {days[cursor].top_offenders
                .slice(0, 3)
                .map((o) => t("reports.daily.offender", { code: o.route_code, min: o.avg_min.toFixed(1) }))
                .join(", ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
