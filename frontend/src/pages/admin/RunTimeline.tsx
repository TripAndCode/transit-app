import { useTranslation } from "react-i18next";
import type { PipelineRun, RebuildHeadline } from "../../api/admin";
import { EM_DASH, fmtPct, formatMinutes } from "../../utils/format";
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
  unchanged: "var(--accent)",
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

// Two decimals, the precision the server rounds them to: a method change can
// move an agency's whole-history average by less than a tenth of a minute.
const DELAY_DIGITS = 2;

function headlineText(t: TFunction, headline: RebuildHeadline): string {
  const { before, after } = headline;
  return t("admin.board.rebuild_headline", {
    onTimeBefore: fmtPct(before.on_time_pct, t),
    onTimeAfter: fmtPct(after.on_time_pct, t),
    delayBefore: formatMinutes(before.avg_delay_min, DELAY_DIGITS),
    delayAfter: formatMinutes(after.avg_delay_min, DELAY_DIGITS),
  });
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
  if (bar.rebuildHeadline) parts.push(headlineText(t, bar.rebuildHeadline));
  return parts.join(" · ");
}

/** The day's rebuilds, each with the agency's headline before and after it,
 *  so what a method change did to the history it rewrote reads without
 *  hovering a bar. Nothing when no run today rebuilt. */
export function RebuildList({ runs, dayStart, now }: { runs: readonly PipelineRun[]; dayStart: Date; now: Date }) {
  const { t } = useTranslation();
  const rebuilds = runsToBars(runs, dayStart, now).flatMap((lane) =>
    lane.bars.flatMap((bar) => (bar.rebuildHeadline ? [{ lane, bar, headline: bar.rebuildHeadline }] : [])),
  );
  if (rebuilds.length === 0) return null;
  return (
    <div style={{ marginTop: 10 }}>
      <h3 style={{ fontSize: 12, fontWeight: 600, margin: "0 0 4px", color: "var(--text-secondary)" }}>
        {t("admin.board.rebuilds_title")}
      </h3>
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {rebuilds.map(({ lane, bar, headline }) => (
          <li
            key={bar.runId}
            data-testid="rebuild-headline"
            style={{ padding: "5px 0", borderTop: "1px solid var(--surface-2)", fontSize: 12 }}
          >
            {t("admin.board.rebuild_item", {
              run: laneLabel(t, lane),
              time: clockLabel(bar.startHour),
              headline: headlineText(t, headline),
            })}{" "}
            <span style={{ color: "var(--text-tertiary)" }}>
              {t("admin.board.rebuild_scope", {
                from: headline.first_day,
                to: headline.last_day,
                preset: headline.preset ?? EM_DASH,
                version: headline.method_version,
              })}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
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
    <div style={{ overflowX: "auto" }}>
      <svg
        data-testid="run-timeline"
        role="img"
        aria-label={t("admin.board.runs_chart_label", { count: runs.length })}
        viewBox={`0 0 ${VIEW_WIDTH} ${height}`}
        // Never narrower than the viewBox, so the 12-unit labels stay at 12px or
        // more; a narrower card scrolls the chart instead.
        style={{ width: "100%", minWidth: VIEW_WIDTH, height: "auto", display: "block", overflow: "visible" }}
      >
        {ticks.map((hour) => (
          <g key={hour}>
            <line x1={hourX(hour)} x2={hourX(hour)} y1={TOP_PAD} y2={axisY} stroke="var(--border-subtle)" />
            <text
              x={hourX(hour)}
              y={height - 6}
              textAnchor="middle"
              fontSize={12}
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
              <text x={LABEL_GUTTER - 8} y={y + BAR_HEIGHT - 3} textAnchor="end" fontSize={12} fill="var(--text-secondary)">
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
            <text x={hourX(marker) + 4} y={TOP_PAD + 8} fontSize={12} fontWeight={600} fill="var(--text-primary)">
              {t("admin.board.runs_now")}
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}
