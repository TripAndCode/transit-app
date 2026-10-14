// @vitest-environment node
import { describe, it, expect } from "vitest";
import { storyVerdict, topRoutesShare } from "./storySentence";
import type { OverviewConcentration } from "../../api/types";

function fakeT(key: string, opts?: Record<string, unknown>) {
  return opts ? `${key}:${JSON.stringify(opts)}` : key;
}

/** A seven-day comparison window, the headline's usual one. */
function h(delta_min: number | null) {
  return { delta_min, window_from: "2026-09-23", window_to: "2026-09-29" };
}

describe("storyVerdict", () => {
  it("states a meaningfully worse period with its delta, in the chip and the sentence", () => {
    expect(storyVerdict(h(0.9), fakeT as never)).toEqual({
      state: 'overview.story.state_behind:{"delta":"0.9"}',
      sentence: 'overview.story.behind:{"delta":"0.9","days":7}',
    });
  });

  it("states a meaningfully better period", () => {
    expect(storyVerdict(h(-1.2), fakeT as never)).toEqual({
      state: 'overview.story.state_ahead:{"delta":"1.2"}',
      sentence: 'overview.story.ahead:{"delta":"1.2","days":7}',
    });
  });

  it("reads a change within the noise threshold as flat", () => {
    expect(storyVerdict(h(0.2), fakeT as never)).toEqual({
      state: 'overview.story.state_flat:{"delta":"0.2"}',
      sentence: 'overview.story.flat:{"delta":"0.2","days":7}',
    });
  });

  it("treats a missing delta as flat rather than guessing a direction", () => {
    expect(storyVerdict(h(null), fakeT as never)).toEqual({
      state: 'overview.story.state_flat:{"delta":"0.0"}',
      sentence: 'overview.story.flat:{"delta":"0.0","days":7}',
    });
  });

  it("counts the comparison window's days from its own dates", () => {
    const verdict = storyVerdict({ delta_min: 0.9, window_from: "2026-09-27", window_to: "2026-09-29" }, fakeT as never);
    expect(verdict.sentence).toBe('overview.story.behind:{"delta":"0.9","days":3}');
  });
});

describe("topRoutesShare", () => {
  const concentration: OverviewConcentration = {
    top_routes: [
      { route_code: "42", route_short_name: null, share_pct: 30 },
      { route_code: "27", route_short_name: null, share_pct: 20.4 },
      { route_code: "15", route_short_name: null, share_pct: 10 },
      { route_code: "9", route_short_name: null, share_pct: 5 },
    ],
    rest_share_pct: 34.6,
    rest_route_count: 3,
  };

  it("sums the top routes' shares, rounded, with how many routes it covers", () => {
    expect(topRoutesShare(concentration)).toEqual({ share: 65, count: 4 });
  });

  it("caps the sum at the routes the concentration card shows", () => {
    const wide: OverviewConcentration = {
      top_routes: ["a", "b", "c", "d", "e", "f"].map((code) => ({ route_code: code, route_short_name: null, share_pct: 10 })),
      rest_share_pct: 40,
      rest_route_count: 0,
    };
    expect(topRoutesShare(wide)).toEqual({ share: 50, count: 5 });
  });

  it("has nothing to say without top routes", () => {
    expect(topRoutesShare({ top_routes: [], rest_share_pct: 0, rest_route_count: 0 })).toBeNull();
    expect(topRoutesShare(null)).toBeNull();
  });
});
