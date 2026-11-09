import type { DefinitionMeta, ReportResponse } from "../api/types";
import { roundTo } from "../utils/format";

type RouteTableRow = Extract<ReportResponse, { report_type: "route_table" }>["rows"][number];
type CouncilReport = Extract<ReportResponse, { report_type: "council_summary" }>;

function asFigure(v: unknown): number | null {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Whether two figures print the same: compared at the `digits` decimals
 *  they are printed with, since a figure is what a reader quotes, and the
 *  API sends some as decimal strings with more places than are printed. A
 *  missing figure equals only another missing one. */
export function sameFigure(a: unknown, b: unknown, digits: number): boolean {
  const x = asFigure(a);
  const y = asFigure(b);
  if (x == null || y == null) return x == null && y == null;
  return roundTo(x, digits) === roundTo(y, digits);
}

/** How one row of a snapshot's table stands against today's: on both sides,
 *  with today's figure for each column (by row index) whose figure prints
 *  differently; only in the snapshot; or only today. */
export type RowMark =
  | { side: "both"; today: ReadonlyMap<number, unknown> }
  | { side: "snapshot" }
  | { side: "today" };

// The Routes table's figure columns and the decimals each prints with
// (ReportTable's route_table schema). The trailing confidence pair is
// derived from the on-time rate and the samples, so it moves only with them.
const ROUTE_TABLE_FIGURES: readonly (readonly [index: number, digits: number])[] = [
  [1, 1],
  [2, 1],
  [3, 1],
  [4, 0],
  [5, 0],
  [6, 1],
];

/** The snapshot's Routes table rows in their stored order, then the routes
 *  only today's lists, in today's order, each with its mark. Rows match by
 *  route. Both sides ran the same sort and row limit, so a route only one
 *  side lists may have moved past that limit rather than left the data. */
export function compareRouteTables(
  snapshot: readonly RouteTableRow[],
  today: readonly RouteTableRow[],
): { rows: RouteTableRow[]; marks: RowMark[] } {
  const todayByRoute = new Map(today.map((r) => [r[0], r]));
  const stored = new Set(snapshot.map((r) => r[0]));
  const marks: RowMark[] = snapshot.map((r) => {
    const now = todayByRoute.get(r[0]);
    if (now == null) return { side: "snapshot" };
    const moved = new Map<number, unknown>();
    for (const [index, digits] of ROUTE_TABLE_FIGURES) {
      if (!sameFigure(r[index], now[index], digits)) moved.set(index, now[index]);
    }
    return { side: "both", today: moved };
  });
  const added = today.filter((r) => !stored.has(r[0]));
  return { rows: [...snapshot, ...added], marks: [...marks, ...added.map((): RowMark => ({ side: "today" }))] };
}

/** How many figures and rows differ: each moved figure, and each row only
 *  one side lists. */
export function differenceCount(marks: readonly RowMark[]): number {
  return marks.reduce((n, m) => n + (m.side === "both" ? m.today.size : 1), 0);
}

/** The council report's headline tiles (CouncilSummaryBlock), by the
 *  structured fields each prints in its value or note. Its prose is
 *  rewritten from these, so the prose itself is never compared. */
export type CouncilTile = "on_time" | "early" | "avg_delay" | "observations" | "planned" | "delivered" | "comparison";

type CouncilFigures = Record<string, { value: unknown; digits: number | null }>;

function councilFigures(report: CouncilReport): Record<CouncilTile, CouncilFigures> {
  const [onTime, avgDelay, samples, planned, , delivered, early] = report.rows[0] ?? [];
  const gate = report.comparability;
  return {
    on_time: { on_time_pct: { value: onTime, digits: 1 } },
    early: { early_departure_pct: { value: early, digits: 1 } },
    avg_delay: { avg_delay_min: { value: avgDelay, digits: 1 } },
    observations: { samples: { value: samples, digits: 0 } },
    planned: {
      planned_trips: { value: planned, digits: 0 },
      covered_days: { value: report.covered_days, digits: 0 },
      window_days: { value: report.window_days, digits: 0 },
    },
    delivered: {
      service_delivered_pct: { value: delivered, digits: 1 },
      delivered_missing: { value: report.delivered_missing ?? null, digits: null },
    },
    comparison: {
      comparable: { value: gate?.comparable ?? null, digits: null },
      reason: { value: gate?.reason ?? null, digits: null },
    },
  };
}

/** The council report's tiles whose figures differ between the snapshot and
 *  today. A figure missing on one side counts as moved. */
export function changedCouncilTiles(snapshot: CouncilReport, today: CouncilReport): Set<CouncilTile> {
  const before = councilFigures(snapshot);
  const after = councilFigures(today);
  const changed = new Set<CouncilTile>();
  for (const tile of Object.keys(before) as CouncilTile[]) {
    const moved = Object.entries(before[tile]).some(([field, { value, digits }]) => {
      const now = after[tile][field].value;
      return digits == null ? value !== now : !sameFigure(value, now, digits);
    });
    if (moved) changed.add(tile);
  }
  return changed;
}

/** Whether two definitions count the same departures on time: the same
 *  early and late bounds, whatever the preset is called. */
export function sameOnTimeWindow(a: DefinitionMeta, b: DefinitionMeta): boolean {
  return a.early_tolerance_sec === b.early_tolerance_sec && a.late_tolerance_sec === b.late_tolerance_sec;
}
