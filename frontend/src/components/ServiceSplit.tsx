// frontend/src/components/ServiceSplit.tsx
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { OverviewServiceSplitDay } from "../api/types";

type Props = {
  service_split: Record<string, number>;
  /** Per-date weekday/weekend rows used by the modal line chart. */
  daily?: OverviewServiceSplitDay[];
  variant?: "card" | "modal";
  onClick?: () => void;
};

const WEEKDAY_KEY = "平日"; // i18n-ignore: GTFS service-type key
const WEEKEND_KEY = "土日祝"; // i18n-ignore: GTFS service-type key

export function ServiceSplit({
  service_split,
  daily,
  variant = "card",
  onClick,
}: Props) {
  const { t } = useTranslation();
  const keys = Object.keys(service_split);
  if (keys.length === 0) return null;

  const values = keys.map((k) => service_split[k]);
  const maxVal = Math.max(...values, 0.0001); // avoid div-by-zero

  // Story + diff annotation (computed only when we have two values).
  let storyNode: React.ReactNode = null;
  let diffNode: React.ReactNode = null;
  if (keys.length >= 2) {
    const weekday = service_split[WEEKDAY_KEY];
    const weekend = service_split[WEEKEND_KEY];
    const sorted = [...values].sort((a, b) => b - a);
    const diff = sorted[0] - sorted[1];
    const pct = sorted[1] > 0 ? (diff / sorted[1]) * 100 : 0;

    if (weekday != null && weekend != null && Math.min(weekday, weekend) > 0) {
      const hi = Math.max(weekday, weekend);
      const lo = Math.min(weekday, weekend);
      const ratio = hi / lo;
      if (ratio < 1.15) {
        storyNode = (
          <p className="ov-svc-story">
            {t("overview.service_split.story_same")}
          </p>
        );
      } else if (weekday > weekend) {
        storyNode = (
          <p className="ov-svc-story">
            {t("overview.service_split.story_weekday_higher", {
              ratio: ratio.toFixed(1),
            })}
          </p>
        );
      } else {
        storyNode = (
          <p className="ov-svc-story">
            {t("overview.service_split.story_weekend_higher", {
              ratio: ratio.toFixed(1),
            })}
          </p>
        );
      }
    }

    if (diff > 0) {
      diffNode = (
        <p className="ov-svc-diff">
          {t("overview.service_split.diff", {
            diff: diff.toFixed(1),
            pct: pct.toFixed(0),
          })}
        </p>
      );
    }
  }

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
      <div className="ov-svc-list">
        {keys.map((k) => {
          const v = service_split[k];
          const pctOfMax = Math.max(0, Math.min(100, (v / maxVal) * 100));
          return (
            <div className="ov-svc-row" key={k}>
              <div className="ov-svc-head">
                <span className="ov-svc-label">
                  {t(`overview.service_split_label.${k}`, { defaultValue: k })}
                </span>
                <span className="ov-svc-num ov-anim-fade">
                  {v.toFixed(1)}
                  {t("overview.hero_unit_min")}
                </span>
              </div>
              <div className="ov-svc-track">
                <div
                  className="ov-svc-fill ov-anim-grow-x"
                  style={{ width: `${pctOfMax}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
      {storyNode}
      {diffNode}
      {variant === "modal" && daily && daily.length > 0 && (
        <ServiceSplitDailyChart daily={daily} />
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Daily line chart (modal only): weekday + weekend over time.
// Two lines, hover tooltip, no chart library.
// --------------------------------------------------------------------------

const DC_W = 830;
const DC_H = 280;
const DC_PAD_LEFT = 36;
const DC_PAD_RIGHT = 16;
const DC_PAD_TOP = 16;
const DC_PAD_BOTTOM = 32;

type DailyHover = {
  visible: boolean;
  svgX: number;
  px: number;
  py: number;
  label: string;
  weekday: number | null;
  weekend: number | null;
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
    label: "",
    weekday: null,
    weekend: null,
  });

  const { xs, weekdayPath, weekendPath, yMax, dateLabels, weekdayCoords, weekendCoords } =
    useMemo(() => {
      const innerW = DC_W - DC_PAD_LEFT - DC_PAD_RIGHT;
      const innerH = DC_H - DC_PAD_TOP - DC_PAD_BOTTOM;
      const stepX =
        daily.length > 1 ? innerW / (daily.length - 1) : 0;
      const toX = (i: number) => DC_PAD_LEFT + i * stepX;
      const allVals: number[] = [];
      for (const d of daily) {
        if (d.weekday != null) allVals.push(d.weekday);
        if (d.weekend != null) allVals.push(d.weekend);
      }
      const yMaxLocal = allVals.length > 0 ? Math.max(...allVals) || 1 : 1;
      const toY = (v: number) =>
        DC_PAD_TOP + (1 - v / yMaxLocal) * innerH;

      const wd: { x: number; y: number; v: number }[] = [];
      const we: { x: number; y: number; v: number }[] = [];
      for (let i = 0; i < daily.length; i++) {
        if (daily[i].weekday != null) {
          wd.push({ x: toX(i), y: toY(daily[i].weekday as number), v: daily[i].weekday as number });
        }
        if (daily[i].weekend != null) {
          we.push({ x: toX(i), y: toY(daily[i].weekend as number), v: daily[i].weekend as number });
        }
      }
      const buildPath = (pts: { x: number; y: number }[]) =>
        pts
          .map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)},${p.y.toFixed(1)}`)
          .join(" ");

      const labels = daily.map((d) => {
        const dt = new Date(d.date + "T00:00:00");
        return `${dt.getMonth() + 1}/${dt.getDate()}`;
      });
      const xsLocal = daily.map((_, i) => toX(i));
      return {
        xs: xsLocal,
        weekdayPath: buildPath(wd),
        weekendPath: buildPath(we),
        yMax: yMaxLocal,
        dateLabels: labels,
        weekdayCoords: wd,
        weekendCoords: we,
      };
    }, [daily]);

  // Silence the unused-var lint for weekdayCoords/weekendCoords; they may
  // be useful later for dot markers but aren't read yet.
  void weekdayCoords;
  void weekendCoords;

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
    const row = daily[best];
    const refY =
      row.weekday != null
        ? DC_PAD_TOP + (1 - row.weekday / yMax) * (DC_H - DC_PAD_TOP - DC_PAD_BOTTOM)
        : row.weekend != null
        ? DC_PAD_TOP + (1 - row.weekend / yMax) * (DC_H - DC_PAD_TOP - DC_PAD_BOTTOM)
        : DC_PAD_TOP;
    setHover({
      visible: true,
      svgX: xs[best],
      px: xs[best] * scaleX,
      py: refY * scaleY,
      label: dateLabels[best],
      weekday: row.weekday,
      weekend: row.weekend,
    });
  }

  if (xs.length === 0) return null;

  const innerH = DC_H - DC_PAD_TOP - DC_PAD_BOTTOM;
  // Y axis ticks at 0, mid, max.
  const yTicks = [0, yMax / 2, yMax];

  return (
    <div className="ov-svc-daily-wrap">
      <div className="ov-svc-daily-legend">
        <span>
          <span
            className="ov-svc-daily-legend-swatch"
            style={{ background: "var(--trend-neutral)" }}
          />
          {t("overview.service_split.weekday_label")}
        </span>
        <span>
          <span
            className="ov-svc-daily-legend-swatch"
            style={{ background: "var(--text-tertiary)" }}
          />
          {t("overview.service_split.weekend_label")}
        </span>
      </div>
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
          const y = DC_PAD_TOP + (1 - v / yMax) * innerH;
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
              <text
                x={DC_PAD_LEFT - 6}
                y={y + 3}
                fontSize="10"
                style={{ fill: "var(--text-tertiary)" }}
                textAnchor="end"
              >
                {v.toFixed(1)}
              </text>
            </g>
          );
        })}
        {/* X tick labels (thinned to ~10) */}
        {xs.map((x, i) => {
          const step = Math.max(1, Math.ceil(xs.length / 10));
          if (i % step !== 0 && i !== xs.length - 1) return null;
          return (
            <text
              key={`xt-${i}`}
              x={x}
              y={DC_H - DC_PAD_BOTTOM + 14}
              fontSize="10"
              style={{ fill: "var(--text-tertiary)" }}
              textAnchor="middle"
            >
              {dateLabels[i]}
            </text>
          );
        })}
        {/* Lines */}
        <path
          d={weekdayPath}
          style={{ stroke: "var(--trend-neutral)" }}
          strokeWidth="1.8"
          fill="none"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d={weekendPath}
          style={{ stroke: "var(--text-tertiary)" }}
          strokeWidth="1.8"
          strokeDasharray="4 3"
          fill="none"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
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
      {hover.visible && (
        <div
          className="ov-tooltip"
          style={{ left: hover.px, top: hover.py }}
        >
          {hover.label} —{" "}
          {hover.weekday != null
            ? `${t("overview.service_split.weekday_label")} ${hover.weekday.toFixed(1)}${t("overview.hero_unit_min")}`
            : "—"}
          {hover.weekend != null
            ? `, ${t("overview.service_split.weekend_label")} ${hover.weekend.toFixed(1)}${t("overview.hero_unit_min")}`
            : ""}
        </div>
      )}
    </div>
  );
}
