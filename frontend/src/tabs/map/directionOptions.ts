import type { TFunction } from "i18next";
import { directionLetter } from "../../utils/directionLetter";
import type { LiveTrip } from "../../api/types";
import type { DirectionOption } from "./OperationsTripPanel";

export function directionKey(trip: LiveTrip): string {
  if (trip.direction_id != null) return `direction:${trip.direction_id}`;
  if (trip.headsign) return `headsign:${trip.headsign}`;
  return "unknown";
}

/** The headsign most trips of a direction carry; a tie goes to the one whose
 *  trip is scheduled earliest, then to the lower text, so row order never decides. */
function commonHeadsign(rows: LiveTrip[]): string | undefined {
  const stats = new Map<string, { count: number; first: string }>();
  for (const { headsign, scheduled_time } of rows) {
    if (!headsign) continue;
    const seen = stats.get(headsign);
    const time = scheduled_time ?? "";
    stats.set(headsign, { count: (seen?.count ?? 0) + 1, first: seen && seen.first <= time ? seen.first : time });
  }
  return [...stats].sort(
    ([a, x], [b, y]) => y.count - x.count || x.first.localeCompare(y.first) || a.localeCompare(b),
  )[0]?.[0];
}

/** A direction is named by its commonest headsign, else "Direction A" or "B",
 *  the letter taken from its direction_id so it survives refetches whatever
 *  order the rows arrive in; an admin also gets the id after it. */
export function directionOptions(
  trips: LiveTrip[],
  t: TFunction,
  isAdmin: boolean,
): DirectionOption[] {
  const grouped = new Map<string, LiveTrip[]>();
  for (const trip of trips) {
    const key = directionKey(trip);
    grouped.set(key, [...(grouped.get(key) ?? []), trip]);
  }
  return [...grouped].map(([key, rows]) => ({
    key,
    label: commonHeadsign(rows) ?? (
      rows[0]?.direction_id != null
        ? t(isAdmin ? "operations.trip_panel.direction_unnamed_admin" : "operations.trip_panel.direction_unnamed", {
            letter: directionLetter(rows[0].direction_id),
            id: rows[0].direction_id,
          })
        : t("operations.trip_panel.unknown_direction")
    ),
    trips: rows.sort((a, b) => (a.scheduled_time ?? "").localeCompare(b.scheduled_time ?? "")),
  }));
}
