import type { DefinitionMeta, ReportResponse } from "../api/types";
import { roundTo } from "../utils/format";
import { councilTiles, type CouncilTile, type TileField } from "./analysis/councilTiles";

type RouteTableReport = Extract<ReportResponse, { report_type: "route_table" }>;
type RouteTableRow = RouteTableReport["rows"][number];
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
// (ReportTable's route_table schema), and whether the column is the
// early-departure rate, which the table shows only beside the threshold it
// counts past. The trailing confidence pair is derived from the on-time rate
// and the samples, so it moves only with them.
const ROUTE_TABLE_FIGURES: readonly { index: number; digits: number; early?: true }[] = [
  { index: 1, digits: 1 },
  { index: 2, digits: 1 },
  { index: 3, digits: 1 },
  { index: 4, digits: 0 },
  { index: 5, digits: 0 },
  { index: 6, digits: 1, early: true },
];

/** The snapshot's Routes table rows in their stored order, then the routes
 *  only today's lists, in today's order, each with its mark. Rows match by
 *  route, and only the columns the snapshot's table shows are compared: an
 *  early-departure rate it never showed is no difference. `capped` says
 *  whether either side's row limit left routes out, the one case where a
 *  route listed on one side only may still be on the other beyond it. */
export function compareRouteTables(
  snapshot: RouteTableReport,
  today: RouteTableReport,
): { rows: RouteTableRow[]; marks: RowMark[]; capped: boolean } {
  const shown = snapshot.definition.early_departure_sec != null;
  const figures = ROUTE_TABLE_FIGURES.filter((f) => shown || !f.early);
  const todayByRoute = new Map(today.rows.map((r) => [r[0], r]));
  const stored = new Set(snapshot.rows.map((r) => r[0]));
  const marks: RowMark[] = snapshot.rows.map((r) => {
    const now = todayByRoute.get(r[0]);
    if (now == null) return { side: "snapshot" };
    const moved = new Map<number, unknown>();
    for (const { index, digits } of figures) {
      if (!sameFigure(r[index], now[index], digits)) moved.set(index, now[index]);
    }
    return { side: "both", today: moved };
  });
  const added = today.rows.filter((r) => !stored.has(r[0]));
  const capped = [snapshot, today].some((side) => side.rows_total != null && side.rows_total > side.rows.length);
  return {
    rows: [...snapshot.rows, ...added],
    marks: [...marks, ...added.map((): RowMark => ({ side: "today" }))],
    capped,
  };
}

/** How many figures and rows differ: each moved figure, and each row only
 *  one side lists. */
export function differenceCount(marks: readonly RowMark[]): number {
  return marks.reduce((n, m) => n + (m.side === "both" ? m.today.size : 1), 0);
}

/** How a council tile stands against today's: its printed values moved, or
 *  only one side has it. */
export type CouncilMark = "changed" | "snapshot" | "today";

function sameField(a: TileField, b: TileField | undefined): boolean {
  if (b == null) return false;
  return a.digits == null ? a.value === b.value : sameFigure(a.value, b.value, a.digits);
}

/** The council report's tiles that read differently today, by the values
 *  each prints (councilTiles), never its prose, which is written from them.
 *  The early-departure tile follows the snapshot's own: one it never showed
 *  is no difference. */
export function compareCouncilTiles(snapshot: CouncilReport, today: CouncilReport): Map<CouncilTile, CouncilMark> {
  const showEarly = snapshot.definition.early_departure_sec != null;
  const before = new Map(councilTiles(snapshot, showEarly).map((tile) => [tile.key, tile.fields]));
  const after = new Map(councilTiles(today, showEarly).map((tile) => [tile.key, tile.fields]));
  const marks = new Map<CouncilTile, CouncilMark>();
  for (const [key, fields] of before) {
    const now = after.get(key);
    if (now == null) marks.set(key, "snapshot");
    else if (!fields.every((field, i) => sameField(field, now[i]))) marks.set(key, "changed");
  }
  for (const key of after.keys()) if (!before.has(key)) marks.set(key, "today");
  return marks;
}

/** Whether two definitions count the same departures on time: the same
 *  early and late bounds, whatever the preset is called. */
export function sameOnTimeWindow(a: DefinitionMeta, b: DefinitionMeta): boolean {
  return a.early_tolerance_sec === b.early_tolerance_sec && a.late_tolerance_sec === b.late_tolerance_sec;
}
