import { useTranslation } from "react-i18next";
import type { PipelineRun } from "../../api/admin";
import { nowMarkerHour, runsToBars, type RunBar, type RunLane } from "./runsTimeline";

type TFunction = ReturnType<typeof useTranslation>["t"];

// A fixed user-space grid scaled by CSS: the lane labels sit inside the SVG,
// so their gutter has to be part of the same coordinate system as the bars.
const VIEW_WIDTH = 640;
const LABEL_GUTTER = 128;
const RIGHT_PAD = 10;
const TOP_PAD = 8;
const LANE_HEIGHT = 24;
const BAR_HEIGHT = 14;
const AXIS_HEIGHT = 22;
const HOURS = 24;
const AXIS_STEP_HOURS = 6;

/** Colour carries urgency only; every bar also states its outcome in a
 *  tooltip, because colour alone is not a label. */
const STATUS_FILL: Record<RunBar["status"], string> = {
  running: "var(--accent)",
  ok: "var(--accent)",
  skipped: "var(--color-warning, #C99A2E)",
  error: "var(--delay-severe)",
};

function hourX(hour: number): number {
  return LABEL_GUTTER + (hour / HOURS) * (VIEW_WIDTH - LABEL_GUTTER - RIGHT_PAD);
}

function clockLabel(hour: number): string {
  const whole = Math.floor(hour);
  const minutes = Math.round((hour - whole) * 60);
  // 24:00 rather than 00:00 for the right-hand edge: the axis labels the end
  // of the day, not the start of the next one.
  return `${String(whole).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

function laneLabel(t: TFunction, lane: RunLane): string {
  const kind = t(`admin.board.run_kind.${lane.kind}`, { defaultValue: lane.kind });
  return lane.agencyName == null
    ? t("admin.board.run_lane_all", { kind })
    : t("admin.board.run_lane", { kind, agency: lane.agencyName });
}

function barTooltip(t: TFunction, lane: string, bar: RunBar): string {
  const parts = [
    t("admin.board.run_tooltip", {
      lane,
      from: clockLabel(bar.startHour),
      to: clockLabel(bar.endHour),
      status: t(`admin.board.run_status.${bar.status}`, { defaultValue: bar.status }),
    }),
  ];
  if (bar.rows != null) parts.push(t("admin.board.run_tooltip_rows", { count: bar.rows }));
  if (bar.lockProbeMs != null) parts.push(t("admin.board.run_tooltip_lock"));
  if (bar.error) parts.push(bar.error);
  return parts.join(" · ");
}

/**
 * The day's runs as a Gantt chart on a 24-hour axis.
 *
 * `dayStart` is the instant the shown day begins and `now` is what closes a
 * still-open run and places the marker; both are passed in rather than read
 * here so the chart is a pure function of its props and the page owns the one
 * idea of "today".
 */
export function RunTimeline({ runs, dayStart, now }: { runs: readonly PipelineRun[]; dayStart: Date; now: Date }) {
  const { t } = useTranslation();
  const lanes = runsToBars(runs, dayStart, now);

  if (lanes.length === 0) {
    return <p style={{ margin: 0, fontSize: 13, color: "var(--text-secondary)" }}>{t("admin.board.runs_empty")}</p>;
  }

  const marker = nowMarkerHour(now, dayStart);
  const axisY = TOP_PAD + lanes.length * LANE_HEIGHT;
  const height = axisY + AXIS_HEIGHT;
  const ticks = Array.from({ length: HOURS / AXIS_STEP_HOURS + 1 }, (_, i) => i * AXIS_STEP_HOURS);

  return (
    <svg
      data-testid="run-timeline"
      role="img"
      aria-label={t("admin.board.runs_chart_label", { count: runs.length })}
      viewBox={`0 0 ${VIEW_WIDTH} ${height}`}
      style={{ width: "100%", height: "auto", display: "block", overflow: "visible" }}
    >
      {ticks.map((hour) => (
        <g key={hour}>
          <line x1={hourX(hour)} x2={hourX(hour)} y1={TOP_PAD} y2={axisY} stroke="var(--border-subtle)" />
          <text
            x={hourX(hour)}
            y={height - 6}
            textAnchor="middle"
            fontSize={10.5}
            fill="var(--text-tertiary)"
          >
            {clockLabel(hour)}
          </text>
        </g>
      ))}

      {lanes.map((lane, index) => {
        const label = laneLabel(t, lane);
        const y = TOP_PAD + index * LANE_HEIGHT;
        return (
          <g key={lane.key} data-testid="run-lane" data-lane={lane.key}>
            <text x={LABEL_GUTTER - 8} y={y + BAR_HEIGHT - 3} textAnchor="end" fontSize={10.5} fill="var(--text-secondary)">
              {label}
            </text>
            {lane.bars.map((bar) => (
              <rect
                key={bar.runId}
                data-testid="run-bar"
                data-status={bar.status}
                data-dashed={String(bar.dashed)}
                x={hourX(bar.startHour)}
                y={y}
                // A run shorter than a pixel of axis still has to be visible:
                // an instant skip is the most important bar on the chart.
                width={Math.max(3, hourX(bar.endHour) - hourX(bar.startHour))}
                height={BAR_HEIGHT}
                rx={3}
                fill={STATUS_FILL[bar.status]}
                fillOpacity={bar.dashed ? 0.35 : 0.9}
                stroke={bar.dashed ? STATUS_FILL[bar.status] : undefined}
                strokeDasharray={bar.dashed ? "3 3" : undefined}
              >
                <title>{barTooltip(t, label, bar)}</title>
              </rect>
            ))}
          </g>
        );
      })}

      {marker != null && (
        <g data-testid="run-now-marker">
          <line
            x1={hourX(marker)}
            x2={hourX(marker)}
            y1={TOP_PAD}
            y2={axisY}
            stroke="var(--text-primary)"
            strokeWidth={1.5}
          />
          <text x={hourX(marker) + 4} y={TOP_PAD + 8} fontSize={10.5} fontWeight={600} fill="var(--text-primary)">
            {t("admin.board.runs_now")}
          </text>
        </g>
      )}
    </svg>
  );
}
