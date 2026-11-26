import { describe, expect, it } from "vitest";
import type { TimetableFitSegment } from "./timetableFitApi";
import { bandRuns, defaultBand, fitBands, stripScale } from "./timetableFitModel";

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

  const hop = (from: string, to: string, seq: number) =>
    segment({ from_stop_id: from, to_stop_id: to, to_stop_sequence: seq });

  it("keeps each direction's segments together, in stop order, where the sequence interleaves them", () => {
    const out1 = hop("A1", "A2", 2);
    const out2 = hop("A2", "A3", 3);
    const back1 = hop("B3", "B2", 2);
    const back2 = hop("B2", "B1", 3);
    const noon = segment({ time_band: "noon" });
    expect(bandRuns([out1, back1, noon, out2, back2], "morning")).toEqual([
      [out1, out2],
      [back1, back2],
    ]);
  });

  it("starts a run where no segment leads in, not at a sequence a mid-route start lowered", () => {
    const head = hop("Z1", "A2", 2);
    const middle = hop("A2", "A3", 3);
    const shortTurn = hop("A3", "A4", 2);
    expect(bandRuns([shortTurn, head, middle], "morning")).toEqual([[head, middle, shortTurn]]);
  });

  it("walks a loop once, from its lowest sequence", () => {
    const a = hop("L1", "L2", 2);
    const b = hop("L2", "L3", 3);
    const c = hop("L3", "L1", 4);
    expect(bandRuns([a, b, c], "morning")).toEqual([[a, b, c]]);
  });

  it("scales the strips to the longest 85th percentile or scheduled time", () => {
    expect(stripScale([segment({ run_p85_sec: 140 }), segment({ run_p85_sec: null, scheduled_run_sec: 180 })])).toBe(180);
    expect(stripScale([segment({ run_p85_sec: null, scheduled_run_sec: null })])).toBe(0);
  });
});
