import { describe, expect, it } from "vitest";
import type { LiveTrip } from "../../api/types";
import { filterLiveRows } from "./liveRowsFilter";

const NOW = Date.parse("2026-09-11T01:10:00Z");

function trip(over: Partial<LiveTrip>): LiveTrip {
  return {
    trip_id: "T1",
    route_code: "R1",
    service_type: "weekday",
    scheduled_time: "10:00:00",
    dep_delay: 0,
    captured_at: "2026-09-11T01:10:00Z",
    stop_id: "S1",
    stop_sequence: 1,
    stop_name: "中央駅",
    stop_lat: 40,
    stop_lon: 140,
    headsign: null,
    ...over,
  };
}

describe("filterLiveRows", () => {
  it("excludes a report captured more than 10 minutes ago", () => {
    const stale = trip({ captured_at: "2026-09-11T00:59:00Z" }); // 11 min old
    expect(filterLiveRows([stale], NOW, [])).toEqual([]);
  });

  it("includes a report captured less than 10 minutes ago", () => {
    const fresh = trip({ captured_at: "2026-09-11T01:01:00Z" }); // 9 min old
    expect(filterLiveRows([fresh], NOW, [])).toEqual([fresh]);
  });

  it("tolerates up to 60 seconds of clock skew into the future", () => {
    const slightlyAhead = trip({ captured_at: "2026-09-11T01:10:45Z" }); // 45s "in the future"
    expect(filterLiveRows([slightlyAhead], NOW, [])).toEqual([slightlyAhead]);
  });

  it("excludes a report more than 60 seconds in the future", () => {
    const wayAhead = trip({ captured_at: "2026-09-11T01:12:00Z" });
    expect(filterLiveRows([wayAhead], NOW, [])).toEqual([]);
  });

  it("excludes a fresh report whose route isn't in a non-empty route filter", () => {
    const other = trip({ route_code: "R2" });
    expect(filterLiveRows([other], NOW, ["R1"])).toEqual([]);
  });

  it("keeps every fresh route when the route filter is empty", () => {
    const a = trip({ route_code: "R1" });
    const b = trip({ route_code: "R2" });
    expect(filterLiveRows([a, b], NOW, [])).toEqual([a, b]);
  });
});
