// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  FLAT_MAX_DELTA_PP,
  avgDelayReferenceJudgement,
  onTimeReferenceJudgement,
  referenceJudgement,
} from "./storySentence";

describe("onTimeReferenceJudgement", () => {
  it("calls a higher on-time rate better and a lower one worse", () => {
    expect(onTimeReferenceJudgement(84.0, 78.0)).toBe("better");
    expect(onTimeReferenceJudgement(72.0, 78.0)).toBe("worse");
  });

  // A fixed tolerance in points: a change under one point is about the same,
  // whatever the reference's rate.
  it("reads a change under one point as about the same, and one point as a change", () => {
    expect(FLAT_MAX_DELTA_PP).toBe(1.0);
    expect(onTimeReferenceJudgement(80.9, 80.0)).toBe("flat");
    expect(onTimeReferenceJudgement(79.1, 80.0)).toBe("flat");
    expect(onTimeReferenceJudgement(81.0, 80.0)).toBe("better");
    expect(onTimeReferenceJudgement(79.0, 80.0)).toBe("worse");
  });

  it("reads an 80% route moving two points as a change, not as under 5% of its rate", () => {
    expect(onTimeReferenceJudgement(82.0, 80.0)).toBe("better");
    expect(onTimeReferenceJudgement(78.0, 80.0)).toBe("worse");
  });

  it("judges by points alone against a rate of 0%", () => {
    expect(onTimeReferenceJudgement(0.9, 0.0)).toBe("flat");
    expect(onTimeReferenceJudgement(1.0, 0.0)).toBe("better");
  });

  it("judges nothing without both rates", () => {
    expect(onTimeReferenceJudgement(null, 80.0)).toBeNull();
    expect(onTimeReferenceJudgement(80.0, null)).toBeNull();
  });
});

describe("avgDelayReferenceJudgement", () => {
  it("judges a route's average delay as Pulse judges its headline", () => {
    for (const [avg, ref] of [
      [3.6, 3.0],
      [2.4, 3.0],
      [0.55, 0.5],
      [9.4, 9.0],
      [0.2, 0],
      [-0.3, 0.3],
    ] as const) {
      const deltaMin = Math.round((avg - ref) * 100) / 100;
      const deltaPct = ref !== 0 ? Math.round((deltaMin / ref) * 1000) / 10 : null;
      expect(avgDelayReferenceJudgement(avg, ref)).toBe(
        referenceJudgement(avg, { avg_min: ref, delta_min: deltaMin, delta_pct: deltaPct }),
      );
    }
    expect(avgDelayReferenceJudgement(3.6, 3.0)).toBe("worse");
    expect(avgDelayReferenceJudgement(9.4, 9.0)).toBe("flat");
    expect(avgDelayReferenceJudgement(-0.3, 0.3)).toBeNull();
  });

  it("judges nothing without both averages", () => {
    expect(avgDelayReferenceJudgement(null, 3.0)).toBeNull();
    expect(avgDelayReferenceJudgement(3.0, null)).toBeNull();
  });
});
