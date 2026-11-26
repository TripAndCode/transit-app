import { describe, expect, it } from "vitest";
import type { TimetableFitSegment } from "./timetableFitApi";
import { bandRows, defaultBand, fitBands, stripScale } from "./timetableFitModel";

function segment(over: Partial<TimetableFitSegment>): TimetableFitSegment {
  return {
    from_stop_id: "S1",
    from_stop_name: null,
    to_stop_id: "S2",
    to_stop_name: null,
    time_band: "morning",
    to_stop_sequence: 2,
    samples: 10,
    run_p50_sec: 100,
    run_p85_sec: 120,
    scheduled_run_sec: 110,
    padding_sec: 10,
    hold_eligible: 0,
    hold_rate: null,
    ...over,
  };
}

describe("timetableFitModel", () => {
  it("lists the bands in clock order, leaving out an untimed segment", () => {
    const segments = [
      segment({ time_band: "evening" }),
      segment({ time_band: null }),
      segment({ time_band: "late_night" }),
      segment({ time_band: "evening", to_stop_id: "S3" }),
    ];
    expect(fitBands(segments)).toEqual(["late_night", "evening"]);
  });

  it("starts on the band with the most runs, the earlier one on a tie", () => {
    expect(defaultBand([segment({ time_band: "evening", samples: 5 }), segment({ time_band: "noon", samples: 9 })])).toBe(
      "noon",
    );
    expect(
      defaultBand([
        segment({ time_band: "evening", samples: 4 }),
        segment({ time_band: "morning", samples: 3 }),
        segment({ time_band: "morning", to_stop_id: "S3", samples: 1 }),
      ]),
    ).toBe("morning");
    expect(defaultBand([segment({ time_band: null })])).toBeNull();
  });

  it("keeps a band's rows in the route's order", () => {
    const a = segment({ to_stop_id: "S2", to_stop_sequence: 2 });
    const b = segment({ to_stop_id: "S3", to_stop_sequence: 3 });
    expect(bandRows([a, segment({ time_band: "noon" }), b], "morning")).toEqual([a, b]);
  });

  it("scales the strips to the longest 85th percentile or scheduled time", () => {
    expect(stripScale([segment({ run_p85_sec: 140 }), segment({ run_p85_sec: null, scheduled_run_sec: 180 })])).toBe(180);
    expect(stripScale([segment({ run_p85_sec: null, scheduled_run_sec: null })])).toBe(0);
  });
});
