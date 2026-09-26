import type { PipelineRun } from "../../api/admin";

/** One drawn bar: a run's span expressed in hours from the start of the day
 *  being shown, so the SVG only has to scale, never to parse dates. */
export type RunBar = {
  runId: number;
  /** Hours from the day's midnight, clamped into [0, 24]. */
  startHour: number;
  /** Where the bar ends: the run's finish, or "now" while it is still open. */
  endHour: number;
  status: PipelineRun["status"];
  /** A run that did no work — displaced by the ingest/analyze advisory lock
   *  — is drawn hollow so it cannot be mistaken for a fast successful one,
   *  which a solid bar of the same width would look exactly like. */
  dashed: boolean;
  rows: number | null;
  lockProbeMs: number | null;
  error: string | null;
};

/** One row of the Gantt: a kind, optionally scoped to an agency. */
export type RunLane = {
  key: string;
  kind: PipelineRun["kind"];
  /** Null for a fleet-wide run; the caller renders its own "all agencies" label. */
  agencyId: number | null;
  agencyName: string | null;
  bars: RunBar[];
};

const HOURS_PER_DAY = 24;
const MS_PER_HOUR = 3_600_000;

/** Reading order down the chart: the order the pipeline actually runs in, so
 *  a morning sweep reads top to bottom as well as left to right. */
const KIND_ORDER: readonly PipelineRun["kind"][] = ["ingest", "analyze", "weather", "static"];

function hoursFrom(dayStart: Date, iso: string | null, fallback: Date): number {
  const at = iso == null ? fallback : new Date(iso);
  return (at.getTime() - dayStart.getTime()) / MS_PER_HOUR;
}

function clampToDay(hour: number): number {
  return Math.min(HOURS_PER_DAY, Math.max(0, hour));
}

function laneRank(kind: PipelineRun["kind"]): number {
  const index = KIND_ORDER.indexOf(kind);
  // A kind added server-side before this build knows it sorts last rather
  // than jumping to the top, which is what a -1 index would do.
  return index === -1 ? KIND_ORDER.length : index;
}

/**
 * Lay out a day's runs as Gantt lanes.
 *
 * `dayStart` is the instant the shown day begins (JST midnight, as an
 * absolute `Date`); `now` closes any run that is still open. Runs that fall
 * entirely outside the day are dropped rather than pinned to an edge — a bar
 * stuck against the axis would claim a run happened at midnight when it did
 * not. One that merely *starts* before the day is clamped, because the part
 * inside the day is real.
 */
export function runsToBars(runs: readonly PipelineRun[], dayStart: Date, now: Date): RunLane[] {
  const lanes = new Map<string, RunLane>();

  for (const run of runs) {
    const rawStart = hoursFrom(dayStart, run.started_at, dayStart);
    const rawEnd = Math.max(rawStart, hoursFrom(dayStart, run.finished_at, now));
    if (rawEnd < 0 || rawStart > HOURS_PER_DAY) continue;

    const key = `${run.kind}:${run.agency_id ?? "all"}`;
    const lane = lanes.get(key) ?? {
      key,
      kind: run.kind,
      agencyId: run.agency_id,
      agencyName: run.agency_name,
      bars: [],
    };
    lane.bars.push({
      runId: run.run_id,
      startHour: clampToDay(rawStart),
      endHour: clampToDay(rawEnd),
      status: run.status,
      dashed: run.status === "skipped",
      rows: run.rows,
      lockProbeMs: run.lock_probe_ms,
      error: run.error,
    });
    lanes.set(key, lane);
  }

  for (const lane of lanes.values()) {
    lane.bars.sort((a, b) => a.startHour - b.startHour || a.runId - b.runId);
  }

  // Fleet-wide first within a kind: it is the umbrella the per-agency lanes
  // below it belong to.
  const agencyRank = (lane: RunLane) => lane.agencyId ?? -1;
  return [...lanes.values()].sort(
    (a, b) => laneRank(a.kind) - laneRank(b.kind) || agencyRank(a) - agencyRank(b),
  );
}

/**
 * Where the "now" marker belongs on this day's axis, or `null` when the day
 * shown is not today — a marker pinned to an edge of a past day would assert
 * a moment that has nothing to do with the runs beneath it.
 */
export function nowMarkerHour(now: Date, dayStart: Date): number | null {
  const hour = (now.getTime() - dayStart.getTime()) / MS_PER_HOUR;
  return hour < 0 || hour > HOURS_PER_DAY ? null : hour;
}
