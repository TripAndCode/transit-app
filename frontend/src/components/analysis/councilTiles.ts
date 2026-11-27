import type { ReportResponse } from "../../api/types";
import { unrankedReasonParts } from "../comparabilityReason";

type CouncilReport = Extract<ReportResponse, { report_type: "council_summary" }>;

/** The council report's headline tiles, by what each shows. */
export type CouncilTile = "on_time" | "early" | "avg_delay" | "observations" | "planned" | "delivered" | "comparison";

/** A value a tile prints, and the decimals it prints with; null digits for a
 *  value printed as words, which only an equal value reads the same as. */
export type TileField = { value: unknown; digits: number | null };

/** The board's verdict as the report carries it, in the shape the reason's
 *  words read. */
export function gateFigures(gate: NonNullable<CouncilReport["comparability"]>) {
  return { ...gate, comparable_reason: gate.reason };
}

/** The council report's headline tiles in the order CouncilSummaryBlock draws
 *  them, each with every value its figure or note prints, so a snapshot
 *  compared with today marks exactly the tiles that read differently. The
 *  early-departure tile shows where `showEarly`, by default where the
 *  report's definition names the threshold that rate counts past; the
 *  comparison tile where the report carries the agencies board's verdict.
 *  A note's values count only where the note is printed. */
export function councilTiles(
  report: CouncilReport,
  showEarly = report.definition.early_departure_sec != null,
): { key: CouncilTile; fields: TileField[] }[] {
  const [onTimePct, avgDelayMin, samples, plannedTrips, , deliveredPct, earlyPct] = report.rows[0] ?? [];
  const { covered_days: covered, window_days: days, delivered_missing: missing } = report;
  const partlyPlanned = !!plannedTrips && covered != null && days != null && covered < days;
  const gate = report.comparability;
  const tiles: { key: CouncilTile; fields: TileField[] }[] = [{ key: "on_time", fields: [{ value: onTimePct, digits: 1 }] }];
  if (showEarly) tiles.push({ key: "early", fields: [{ value: earlyPct, digits: 1 }] });
  tiles.push(
    { key: "avg_delay", fields: [{ value: avgDelayMin, digits: 1 }] },
    { key: "observations", fields: [{ value: samples, digits: 0 }] },
    {
      key: "planned",
      fields: [
        { value: plannedTrips, digits: 0 },
        { value: partlyPlanned ? `${covered}/${days}` : null, digits: null },
      ],
    },
    {
      key: "delivered",
      fields: [
        { value: deliveredPct, digits: 1 },
        { value: deliveredPct == null ? (missing ?? null) : null, digits: null },
      ],
    },
  );
  if (gate) {
    const reason = gate.comparable ? null : unrankedReasonParts(gateFigures(gate), gate.thresholds);
    tiles.push({
      key: "comparison",
      fields: [
        { value: gate.comparable, digits: null },
        { value: reason == null ? null : JSON.stringify(reason), digits: null },
      ],
    });
  }
  return tiles;
}
