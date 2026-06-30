// frontend/src/components/PeakHourRibbon.tsx
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { OverviewPeakHour } from "../api/types";

type Props = {
  peak_hour: OverviewPeakHour | null;
  /** Weekday-only profile, used by the modal split view. */
  peak_hour_weekday?: OverviewPeakHour | null;
  /** Weekend-only profile, used by the modal split view. */
  peak_hour_weekend?: OverviewPeakHour | null;
  variant?: "card" | "modal";
  onClick?: () => void;
  /** Called when the user clicks on a specific hour bar in the chart. */
  onHourClick?: (hour: number) => void;
};

const W = 660;
const H = 140;
const PAD_TOP = 28; // headroom for max-label callout
const PAD_BOTTOM = 22;
const PAD_LEFT = 0;
const PAD_RIGHT = 32; // space for "avg" label at the right edge
const CELL_W = (W - PAD_LEFT - PAD_RIGHT) / 24;

type HoverState = {
  visible: boolean;
  svgX: number;
  px: number;
  py: number;
  label: string;
  value: string;
};

/** Standalone 24-bar peak-hour chart. Used by both card and modal,
 *  and rendered twice in the modal (once per DOW partition). */
function PeakHourChart({
  peak_hour,
  onHourClick,
}: {
  peak_hour: OverviewPeakHour;
  onHourClick?: (hour: number) => void;
}) {
  const { t } = useTranslation();
  const [hover, setHover] = useState<HoverState>({
    visible: false,
    svgX: 0,
    px: 0,
    py: 0,
    label: "",
    value: "",
  });

  const hourValues = peak_hour.by_hour;
  const nonNull = hourValues.filter((v): v is number => v != null);
  const overallAvg =
    nonNull.length > 0
      ? nonNull.reduce((s, v) => s + v, 0) / nonNull.length
      : 0;

  const denom = peak_hour.peak_avg_min || 1;
  const usableH = H - PAD_TOP - PAD_BOTTOM;
  const toY = (v: number) => H - PAD_BOTTOM - (v / denom) * usableH;

  const peakIdx = peak_hour.peak_hour;
  const peakV = hourValues[peakIdx] ?? peak_hour.peak_avg_min;
  const peakBarX = PAD_LEFT + peakIdx * CELL_W;
  const peakBarY = toY(peakV);

  const avgY = toY(overallAvg);

  const showSpread = nonNull.length >= 3 && overallAvg > 0;
  type Segment = { startHour: number; endHour: number };
  const spreadSegments: Segment[] = [];
  if (showSpread) {
    let segStart: number | null = null;
    for (let h = 0; h < 24; h++) {
      const v = hourValues[h];
      const isWorse = v != null && v > overallAvg;
      if (isWorse && segStart == null) segStart = h;
      if (!isWorse && segStart != null) {
        spreadSegments.push({ startHour: segStart, endHour: h });
        segStart = null;
      }
    }
    if (segStart != null)
      spreadSegments.push({ startHour: segStart, endHour: 24 });
  }

  function handleMove(e: React.MouseEvent<SVGSVGElement>) {
    const svg = e.currentTarget;
    const rect = svg.getBoundingClientRect();
    const scaleX = rect.width / W;
    const scaleY = rect.height / H;
    const localX = (e.clientX - rect.left) / scaleX;
    const idx = Math.max(
      0,
      Math.min(23, Math.floor((localX - PAD_LEFT) / CELL_W)),
    );
    const v = hourValues[idx];
    if (v == null) {
      setHover((h) => ({ ...h, visible: false }));
      return;
    }
    const barX = PAD_LEFT + idx * CELL_W + CELL_W / 2;
    const barY = toY(v);
    setHover({
      visible: true,
      svgX: barX,
      px: barX * scaleX,
      py: barY * scaleY,
      label: `${idx}:00`,
      value: `${v.toFixed(1)}${t("overview.hero_unit_min")}`,
    });
  }

  function handleClick(e: React.MouseEvent<SVGSVGElement>) {
    if (!onHourClick) return;
    const svg = e.currentTarget;
    const rect = svg.getBoundingClientRect();
    const scaleX = rect.width / W;
    const localX = (e.clientX - rect.left) / scaleX;
    const idx = Math.max(0, Math.min(23, Math.floor((localX - PAD_LEFT) / CELL_W)));
    if (hourValues[idx] != null) onHourClick(idx);
  }

  return (
    <div
      className="ov-peak-svg-wrap"
      onMouseLeave={() => setHover((h) => ({ ...h, visible: false }))}
      style={{ cursor: onHourClick ? "pointer" : "default" }}
    >
      <svg
        width="100%"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        style={{ display: "block", overflow: "visible" }}
        role="img"
        aria-label={t("overview.section_peak_hour")}
        onMouseMove={handleMove}
        onClick={handleClick}
      >
        {spreadSegments.map((seg, i) => (
          <rect
            key={`spread-${i}`}
            className="ov-peak-spread"
            x={PAD_LEFT + seg.startHour * CELL_W}
            y={PAD_TOP - 4}
            width={(seg.endHour - seg.startHour) * CELL_W}
            height={H - PAD_BOTTOM - (PAD_TOP - 4)}
          />
        ))}

        {hourValues.map((v, h) => {
          if (v == null) return null;
          const x = PAD_LEFT + h * CELL_W + 1;
          const y = toY(v);
          const bar_h = Math.max(H - PAD_BOTTOM - y, 0);
          const isPeak = h === peakIdx;
          const fill = isPeak ? "#b45309" : "#475569";
          const opacity = isPeak ? 0.95 : 0.3;
          return (
            <rect
              key={h}
              x={x}
              y={y}
              width={CELL_W - 2}
              height={bar_h}
              fill={fill}
              opacity={opacity}
              rx={2}
              ry={2}
            />
          );
        })}

        {overallAvg > 0 && (
          <>
            <line
              x1={PAD_LEFT}
              y1={avgY}
              x2={W - PAD_RIGHT}
              y2={avgY}
              stroke="#cbd5e1"
              strokeWidth="1"
              strokeDasharray="4 4"
            />
            <text
              x={W - PAD_RIGHT + 4}
              y={avgY + 3}
              fontSize="10"
              fill="#94a3b8"
              textAnchor="start"
            >
              {t("overview.peak_hour.avg_label")}
            </text>
          </>
        )}

        <g>
          <line
            x1={peakBarX + CELL_W / 2}
            y1={peakBarY - 2}
            x2={peakBarX + CELL_W / 2}
            y2={peakBarY - 12}
            stroke="#b45309"
            strokeWidth="1"
          />
          <line
            x1={peakBarX + CELL_W / 2}
            y1={peakBarY - 12}
            x2={peakBarX + CELL_W / 2 + 4}
            y2={peakBarY - 12}
            stroke="#b45309"
            strokeWidth="1"
          />
          <text
            x={peakBarX + CELL_W / 2 + 6}
            y={peakBarY - 9}
            fontSize="11"
            fontWeight="600"
            fill="#b45309"
            textAnchor="start"
          >
            {t("overview.peak_hour.max_label", {
              avg: peak_hour.peak_avg_min.toFixed(1),
            })}
          </text>
        </g>

        <line
          x1={PAD_LEFT}
          y1={H - PAD_BOTTOM}
          x2={W - PAD_RIGHT}
          y2={H - PAD_BOTTOM}
          stroke="#e5e7eb"
          strokeWidth="1"
        />

        {[0, 6, 12, 18].map((h) => (
          <text
            key={h}
            x={PAD_LEFT + h * CELL_W + CELL_W / 2}
            y={H - 6}
            fontSize="10"
            fill="#8e8e93"
            textAnchor="middle"
          >
            {h}
          </text>
        ))}

        {hover.visible && (
          <line
            x1={hover.svgX}
            y1={PAD_TOP - 2}
            x2={hover.svgX}
            y2={H - PAD_BOTTOM + 2}
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
    </div>
  );
}

export function PeakHourRibbon({
  peak_hour,
  peak_hour_weekday,
  peak_hour_weekend,
  variant = "card",
  onClick,
  onHourClick,
}: Props) {
  const { t } = useTranslation();
  if (peak_hour == null) return null;

  const clickable = !!onClick;
  const wrapperClass =
    variant === "modal"
      ? "ov-peak-modal"
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

  if (variant === "modal") {
    return (
      <div className={wrapperClass} {...interactiveProps}>
        <p className="ov-modal-explainer">{t("overview.peak_explainer")}</p>
        <div className="ov-peak-dow-stack">
          <div>
            <p className="ov-peak-dow-panel-title">
              {t("overview.peak_hour.weekday_label")}
            </p>
            {peak_hour_weekday ? (
              <PeakHourChart peak_hour={peak_hour_weekday} />
            ) : (
              <p className="ov-peak-dow-empty">
                {t("overview.peak_hour.weekday_empty")}
              </p>
            )}
          </div>
          <div>
            <p className="ov-peak-dow-panel-title">
              {t("overview.peak_hour.weekend_label")}
            </p>
            {peak_hour_weekend ? (
              <PeakHourChart peak_hour={peak_hour_weekend} />
            ) : (
              <p className="ov-peak-dow-empty">
                {t("overview.peak_hour.weekend_empty")}
              </p>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={wrapperClass} {...interactiveProps}>
      <p className="ov-card-eyebrow">{t("overview.section_peak_hour")}</p>
      <PeakHourChart peak_hour={peak_hour} onHourClick={onHourClick} />
      <p className="ov-pareto-rest" style={{ marginTop: 10 }}>
        {t("overview.peak_hour_callout", {
          hour: peak_hour.peak_hour,
          next_hour: peak_hour.peak_hour + 1,
          avg: peak_hour.peak_avg_min.toFixed(1),
        })}
      </p>
    </div>
  );
}
