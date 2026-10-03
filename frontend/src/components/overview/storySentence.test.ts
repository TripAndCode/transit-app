// @vitest-environment node
import { describe, it, expect } from "vitest";
import { storySentence } from "./storySentence";
import type { OverviewConcentration, OverviewPeakHour } from "../../api/types";

function fakeT(key: string, opts?: Record<string, unknown>) {
  return opts ? `${key}:${JSON.stringify(opts)}` : key;
}

const peak: OverviewPeakHour = { by_hour: [], peak_hour: 17, peak_avg_min: 4.8 };

const concentration: OverviewConcentration = {
  top_routes: [
    { route_code: "42", route_short_name: null, share_pct: 30 },
    { route_code: "27", route_short_name: null, share_pct: 20 },
    { route_code: "15", route_short_name: null, share_pct: 10 },
    { route_code: "9", route_short_name: null, share_pct: 5 },
  ],
  rest_share_pct: 35,
  rest_route_count: 3,
};

describe("storySentence", () => {
  it("selects the behind template when delay is meaningfully worse than last week", () => {
    const result = storySentence({ delta_min: 0.9 }, peak, concentration, fakeT as never);
    expect(result).toBe('overview.story.behind:{"delta":"0.9","peak":17,"share":60,"count":3}');
  });

  it("selects the ahead template when delay is meaningfully better than last week", () => {
    const result = storySentence({ delta_min: -1.2 }, peak, concentration, fakeT as never);
    expect(result).toBe('overview.story.ahead:{"delta":"1.2","peak":17,"share":60,"count":3}');
  });

  it("selects the flat template when the change is within the noise threshold", () => {
    const result = storySentence({ delta_min: 0.2 }, peak, concentration, fakeT as never);
    expect(result).toBe('overview.story.flat:{"delta":"0.2","peak":17,"share":60,"count":3}');
  });

  it("treats a missing delta as flat rather than guessing a direction", () => {
    const result = storySentence({ delta_min: null }, peak, concentration, fakeT as never);
    expect(result).toBe('overview.story.flat:{"delta":"0.0","peak":17,"share":60,"count":3}');
  });

  it("falls back to the short template when peak-hour data is missing", () => {
    const result = storySentence({ delta_min: 0.9 }, null, concentration, fakeT as never);
    expect(result).toBe('overview.story.behind_short:{"delta":"0.9"}');
  });

  it("falls back to the short template when concentration has no top routes", () => {
    const empty: OverviewConcentration = { top_routes: [], rest_share_pct: 0, rest_route_count: 0 };
    const result = storySentence({ delta_min: -1.2 }, peak, empty, fakeT as never);
    expect(result).toBe('overview.story.ahead_short:{"delta":"1.2"}');
  });

  it("falls back to the short template when concentration itself is null", () => {
    const result = storySentence({ delta_min: 0.2 }, peak, null, fakeT as never);
    expect(result).toBe('overview.story.flat_short:{"delta":"0.2"}');
  });

  it("caps the concentration clause at the top 3 routes even when more are given", () => {
    const wide: OverviewConcentration = {
      top_routes: [
        { route_code: "a", route_short_name: null, share_pct: 10 },
        { route_code: "b", route_short_name: null, share_pct: 10 },
        { route_code: "c", route_short_name: null, share_pct: 10 },
        { route_code: "d", route_short_name: null, share_pct: 10 },
        { route_code: "e", route_short_name: null, share_pct: 10 },
      ],
      rest_share_pct: 50,
      rest_route_count: 0,
    };
    const result = storySentence({ delta_min: 0.9 }, peak, wide, fakeT as never);
    expect(result).toBe('overview.story.behind:{"delta":"0.9","peak":17,"share":30,"count":3}');
  });
});
