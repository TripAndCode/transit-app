import type { TFunction } from "i18next";
import type { LiveTrip } from "../../api/types";
import type { DirectionOption } from "./OperationsTripPanel";

export function directionKey(trip: LiveTrip): string {
  if (trip.direction_id != null) return `direction:${trip.direction_id}`;
  if (trip.headsign) return `headsign:${trip.headsign}`;
  return "unknown";
}

/** A direction with no headsign is told apart from its sibling by position,
 *  never by its GTFS direction_id; an admin also gets that id after it. */
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
  let unnamed = 0;
  return [...grouped].map(([key, rows]) => ({
    key,
    label: rows.find((trip) => trip.headsign)?.headsign ?? (
      rows[0]?.direction_id != null
        ? t(isAdmin ? "operations.trip_panel.direction_unnamed_admin" : "operations.trip_panel.direction_unnamed", {
            letter: String.fromCharCode(65 + (unnamed++ % 26)),
            id: rows[0].direction_id,
          })
        : t("operations.trip_panel.unknown_direction")
    ),
    trips: rows.sort((a, b) => (a.scheduled_time ?? "").localeCompare(b.scheduled_time ?? "")),
  }));
}
