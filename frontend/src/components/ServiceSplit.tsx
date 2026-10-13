import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { OverviewDayType, OverviewServiceSplitDay, OverviewServiceSplitEntry } from "../api/types";
import { FILTER_SEPARATOR, formatMinutes, formatNumber, formatShortDate, roundTenth } from "../utils/format";
import type { LabelT } from "../utils/filterValueLabels";
import { niceAxis } from "./charts/niceAxis";
import { showsLabel } from "./analysis/axisLabels";

type Props = {
  /** All three day types, in display order. */
  service_split: OverviewServiceSplitEntry[];
  /** One row per date with data, used by the modal line chart. */
  daily?: OverviewServiceSplitDay[];
  variant?: "card" | "modal";
  onClick?: () => void;
};

const DAY_TYPE_KEY: Record<OverviewDayType, string> = {
  weekday: "overview.service_split.type.weekday",
  saturday: "overview.service_split.type.saturday",
  sunday_holiday: "overview.service_split.type.sunday_holiday",
};

function dayTypeLabel(type: OverviewDayType, t: LabelT): string {
  return t(DAY_TYPE_KEY[type]);
}

/** Two tied day types read as one subject ("Weekdays and Saturdays"). */
function joinLabels(types: OverviewDayType[], t: LabelT): string {
  const labels = types.map((type) => dayTypeLabel(type, t));
  return labels.length === 2 ? t("overview.service_split.pair", { a: labels[0], b: labels[1] }) : labels[0];
}

/** The slowest day type against the fastest, judged on the one-decimal means
 *  the rows show so the sentence never disagrees with them. Null when fewer
 *  than two day types have data. */
function story(entries: OverviewServiceSplitEntry[], t: LabelT): string | null {
  const shown = entries.flatMap((e) => (e.avg_min == null ? [] : [{ type: e.day_type, v: roundTenth(e.avg_min) }]));
  if (shown.length < 2) return null;
  const hi = Math.max(...shown.map((s) => s.v));
  const lo = Math.min(...shown.map((s) => s.v));
  if (hi === lo) return t("overview.service_split.story_same");
  return t("overview.service_split.story_slowest", {
    slowest: joinLabels(shown.filter((s) => s.v === hi).map((s) => s.type), t),
    fastest: joinLabels(shown.filter((s) => s.v === lo).map((s) => s.type), t),
    diff: formatMinutes(hi - lo),
  });
}

/** The shared bar scale: always spans zero, so an early-running (negative)
 *  mean draws leftward from it rather than off the track. */
function barAxis(entries: OverviewServiceSplitEntry[]) {
  const values = entries.flatMap((e) => (e.avg_min == null ? [] : [e.avg_min]));
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  return high > low ? niceAxis(low, high, 4) : niceAxis(0, 1, 4);
}

function metaText(entry: OverviewServiceSplitEntry, t: LabelT): string {
  if (entry.samples === 0) return t("overview.service_split.no_data");
  const samples = formatNumber(entry.samples, { notation: "compact" });
  return [
    t("overview.service_split.days", { count: entry.days }),
    t("overview.service_split.samples", { value: samples }),
  ].join(FILTER_SEPARATOR);
}

export function ServiceSplit({
  service_split,
  daily,
  variant = "card",
  onClick,
}: Props) {
  const { t } = useTranslation();
  if (service_split.length === 0) return null;

  const axis = barAxis(service_split);
  const span = axis.high - axis.low;
  const pctOf = (v: number) => ((v - axis.low) / span) * 100;
  const storyText = story(service_split, t);

  const clickable = !!onClick;
  const wrapperClass =
    variant === "modal"
      ? "ov-svc-modal"
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

  return (
    <div className={wrapperClass} {...interactiveProps}>
      {variant !== "modal" && (
        <p className="ov-card-eyebrow">
          {t("overview.section_service_split")}
        </p>
      )}
      {variant === "modal" && (
        <p className="ov-modal-explainer">
          {t("overview.service_split_explainer")}
        </p>
      )}
      {storyText && <p className="ov-svc-story">{storyText}</p>}
      <div className="ov-svc-list">
        {service_split.map((entry) => {
          const v = entry.avg_min;
          return (
            <div
              className={`ov-svc-row${v == null ? " ov-svc-row--empty" : ""}`}
              key={entry.day_type}
            >
              <span className="ov-svc-label">{dayTypeLabel(entry.day_type, t)}</span>
              <span className="ov-svc-track" aria-hidden="true">
                {v != null && (
                  <span
                    className="ov-svc-fill"
                    style={{
                      left: `${pctOf(Math.min(0, v))}%`,
                      width: `${(Math.abs(v) / span) * 100}%`,
                    }}
                  />
                )}
              </span>
              <span className="ov-svc-num num">{formatMinutes(v)}</span>
              <span className="ov-svc-meta num">{metaText(entry, t)}</span>
            </div>
          );
        })}
        <div className="ov-svc-scale" aria-hidden="true">
          {axis.ticks.map((tick, i) => {
            const last = i === axis.ticks.length - 1;
            const value = formatNumber(tick);
            return (
              <span
                key={tick}
                className={`ov-svc-tick${i === 0 ? " ov-svc-tick--first" : ""}${last ? " ov-svc-tick--last" : ""} num`}
                style={{ left: `${pctOf(tick)}%` }}
              >
                {last ? t("common.minutes_value", { value }) : value}
              </span>
            );
          })}
        </div>
      </div>
      {variant === "modal" && daily && daily.length > 0 && (
        <ServiceSplitDailyChart daily={daily} />
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Daily line chart (modal only): one series per day type over time.
// Every date belongs to exactly one day type, so each series joins only its
// own dates; marks keep a series with a single date (one Saturday in range)
// visible. Hover tooltip, no chart library.
// --------------------------------------------------------------------------

const DC_W = 830;
const DC_H = 280;
const DC_PAD_LEFT = 36;
const DC_PAD_RIGHT = 16;
const DC_PAD_TOP = 16;
const DC_PAD_BOTTOM = 32;

const SERIES: { type: OverviewDayType; stroke: string; dash: string }[] = [
  { type: "weekday", stroke: "var(--trend-neutral)", dash: "0" },
  { type: "saturday", stroke: "var(--accent)", dash: "6 3" },
  { type: "sunday_holiday", stroke: "var(--text-tertiary)", dash: "2 3" },
];

type DailyHover = {
  visible: boolean;
  svgX: number;
  px: number;
  py: number;
  index: number;
};

function ServiceSplitDailyChart({
  daily,
}: {
  daily: OverviewServiceSplitDay[];
}) {
  const { t } = useTranslation();
  const [hover, setHover] = useState<DailyHover>({
    visible: false,
    svgX: 0,
    px: 0,
    py: 0,
    index: 0,
  });

  const innerW = DC_W - DC_PAD_LEFT - DC_PAD_RIGHT;
  const innerH = DC_H - DC_PAD_TOP - DC_PAD_BOTTOM;
  const stepX = daily.length > 1 ? innerW / (daily.length - 1) : 0;
  const toX = (i: number) => DC_PAD_LEFT + i * stepX;
  const allVals = daily.map((d) => d.avg_min);
  // The axis always spans zero: early-running (negative) means draw below it
  // rather than outside the plot, and an all-negative series keeps its
  // ordering instead of inverting the scale.
  const yMin = Math.min(0, ...allVals);
  const yMax = Math.max(0, ...allVals) || 1;
  const toY = (v: number) => DC_PAD_TOP + ((yMax - v) / (yMax - yMin)) * innerH;

  const points = daily.map((d, i) => ({ x: toX(i), y: toY(d.avg_min), type: d.day_type }));
  const buildPath = (pts: { x: number; y: number }[]) =>
    pts
      .map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)},${p.y.toFixed(1)}`)
      .join(" ");

  const dateLabels = daily.map((d) => formatShortDate(d.date));
  const xs = points.map((p) => p.x);

  function handleMove(e: React.MouseEvent<SVGSVGElement>) {
    const svg = e.currentTarget;
    const rect = svg.getBoundingClientRect();
    const scaleX = rect.width / DC_W;
    const scaleY = rect.height / DC_H;
    const localX = (e.clientX - rect.left) / scaleX;
    let best = 0;
    let bestDist = Infinity;
    xs.forEach((x, i) => {
      const d = Math.abs(x - localX);
      if (d < bestDist) {
        bestDist = d;
        best = i;
      }
    });
    setHover({
      visible: true,
      svgX: xs[best],
      px: xs[best] * scaleX,
      py: points[best].y * scaleY,
      index: best,
    });
  }

  if (xs.length === 0) return null;

  // Y axis ticks at the axis minimum, middle and maximum.
  const yTicks = [yMin, (yMin + yMax) / 2, yMax];
  const hovered = hover.visible ? daily[hover.index] : null;

  return (
    <div className="ov-svc-daily-wrap">
      <div className="ov-svc-daily-legend">
        {SERIES.map((s) => (
          <span key={s.type}>
            <svg className="ov-svc-daily-legend-swatch" width="18" height="4" aria-hidden="true">
              <line x1="1" y1="2" x2="17" y2="2" style={{ stroke: s.stroke }} strokeWidth="2" strokeDasharray={s.dash} />
            </svg>
            {dayTypeLabel(s.type, t)}
          </span>
        ))}
      </div>
      <div className="ov-chart-plot">
      <svg
        width="100%"
        viewBox={`0 0 ${DC_W} ${DC_H}`}
        role="img"
        aria-label={t("overview.service_split.daily_label")}
        style={{ display: "block", overflow: "visible" }}
        onMouseMove={handleMove}
        onMouseLeave={() => setHover((h) => ({ ...h, visible: false }))}
      >
        {/* Y gridlines */}
        {yTicks.map((v, i) => {
          const y = toY(v);
          return (
            <g key={`yg-${i}`}>
              <line
                x1={DC_PAD_LEFT}
                y1={y}
                x2={DC_W - DC_PAD_RIGHT}
                y2={y}
                style={{ stroke: "var(--border-subtle)" }}
                strokeWidth="1"
                strokeDasharray={i === 0 ? "0" : "2 4"}
              />
            </g>
          );
        })}
        {SERIES.map((s) => {
          const own = points.filter((p) => p.type === s.type);
          return (
            <g key={s.type}>
              <path
                d={buildPath(own)}
                style={{ stroke: s.stroke }}
                strokeWidth="1.8"
                strokeDasharray={s.dash}
                fill="none"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              {own.map((p) => (
                <circle
                  key={p.x}
                  cx={p.x}
                  cy={p.y}
                  r="2.5"
                  data-day-type={s.type}
                  style={{ fill: s.stroke }}
                />
              ))}
            </g>
          );
        })}
        {hover.visible && (
          <line
            x1={hover.svgX}
            y1={DC_PAD_TOP}
            x2={hover.svgX}
            y2={DC_H - DC_PAD_BOTTOM}
            style={{ stroke: "var(--trend-neutral)", strokeOpacity: 0.30 }}
            strokeWidth="1"
          />
        )}
      </svg>
      {/* HTML axis labels (see .ov-chart-plot in overview.css). */}
      {yTicks.map((v, i) => (
        <span
          key={`yt-${i}`}
          className="ov-svc-daily-label ov-svc-daily-label--y num"
          style={{
            top: `${(toY(v) / DC_H) * 100}%`,
            left: `${((DC_PAD_LEFT - 6) / DC_W) * 100}%`,
          }}
        >
          {v.toFixed(1)}
        </span>
      ))}
      {xs.map((x, i) => {
        if (!showsLabel(i, xs.length, 10)) return null;
        return (
          <span
            key={`xt-${i}`}
            className="ov-svc-daily-label ov-svc-daily-label--x num"
            style={{
              top: `${((DC_H - DC_PAD_BOTTOM + 14) / DC_H) * 100}%`,
              left: `${(x / DC_W) * 100}%`,
            }}
          >
            {dateLabels[i]}
          </span>
        );
      })}
      {/* Inside the plot box it is measured against. */}
      {hovered && (
        <div
          className="ov-tooltip"
          style={{ left: hover.px, top: hover.py }}
        >
          {dateLabels[hover.index]} — {dayTypeLabel(hovered.day_type, t)} {formatMinutes(hovered.avg_min)}
        </div>
      )}
      </div>
    </div>
  );
}
