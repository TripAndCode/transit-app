import { describe, it, expect } from "vitest";
import i18n from "../../i18n";
import type { LiveTrip } from "../../api/types";
import { directionOptions } from "./directionOptions";

const t = i18n.getFixedT("en");
const trip = (over: Partial<LiveTrip>) => ({ trip_id: "T", scheduled_time: "08:00", headsign: null, direction_id: null, ...over }) as LiveTrip;
const TRIPS = [trip({ trip_id: "a", direction_id: 0 }), trip({ trip_id: "b", direction_id: 1 }), trip({ trip_id: "c", direction_id: 1, headsign: "Harbour" })];

describe("directionOptions", () => {
  it("names a direction by its headsign and tells an unnamed one apart without its GTFS id", () => {
    expect(directionOptions(TRIPS, t, false).map((d) => d.label)).toEqual(["Direction A", "Harbour"]);
  });

  it("labels each direction from its id, so a refetch in another row order relabels nothing", () => {
    const forward = directionOptions([trip({ trip_id: "a", direction_id: 0 }), trip({ trip_id: "b", direction_id: 1 })], t, false);
    const reversed = directionOptions([trip({ trip_id: "b", direction_id: 1 }), trip({ trip_id: "a", direction_id: 0 })], t, false);
    const byKey = (opts: typeof forward) => Object.fromEntries(opts.map((d) => [d.key, d.label]));
    expect(byKey(reversed)).toEqual(byKey(forward));
    expect(byKey(forward)).toEqual({ "direction:0": "Direction A", "direction:1": "Direction B" });
  });

  it("keeps the direction_id as secondary text for an admin", () => {
    expect(directionOptions(TRIPS, t, true).map((d) => d.label)).toEqual(["Direction A (direction_id 0)", "Harbour"]);
  });

  it("says a trip with neither headsign nor direction is unknown", () => {
    expect(directionOptions([trip({})], t, false).map((d) => d.label)).toEqual(["Direction unknown"]);
  });
});
