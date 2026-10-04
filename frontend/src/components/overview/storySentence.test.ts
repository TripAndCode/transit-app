// @vitest-environment node
import { describe, it, expect } from "vitest";
import { storySentence } from "./storySentence";
import type { OverviewConcentration, OverviewPeakHour } from "../../api/types";

function fakeT(key: string, opts?: Record<string, unknown>) {
  return opts ? `${key}:${JSON.stringify(opts)}` : key;
}

const peak: OverviewPeakHour = { by_hour: [], peak_hour: 17, peak_avg_min: 4.8 };

/** A seven-day comparison window, the headline's usual one. */
function h(delta_min: number | null) {
  return { delta_min, window_from: "2026-09-23", window_to: "2026-09-29" };
}

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
  it("selects the behind template when delay is meaningfully worse than the window before", () => {
    const result = storySentence(h(0.9), peak, concentration, fakeT as never);
    expect(result).toBe('overview.story.behind:{"delta":"0.9","days":7,"peak":"17:00–18:00","share":65,"count":4}');
  });

  it("selects the ahead template when delay is meaningfully better than the window before", () => {
    const result = storySentence(h(-1.2), peak, concentration, fakeT as never);
    expect(result).toBe('overview.story.ahead:{"delta":"1.2","days":7,"peak":"17:00–18:00","share":65,"count":4}');
  });

  it("selects the flat template when the change is within the noise threshold", () => {
    const result = storySentence(h(0.2), peak, concentration, fakeT as never);
    expect(result).toBe('overview.story.flat:{"delta":"0.2","days":7,"peak":"17:00–18:00","share":65,"count":4}');
  });

  it("treats a missing delta as flat rather than guessing a direction", () => {
    const result = storySentence(h(null), peak, concentration, fakeT as never);
    expect(result).toBe('overview.story.flat:{"delta":"0.0","days":7,"peak":"17:00–18:00","share":65,"count":4}');
  });

  it("falls back to the short template when peak-hour data is missing", () => {
    const result = storySentence(h(0.9), null, concentration, fakeT as never);
    expect(result).toBe('overview.story.behind_short:{"delta":"0.9","days":7}');
  });

  it("falls back to the short template when concentration has no top routes", () => {
    const empty: OverviewConcentration = { top_routes: [], rest_share_pct: 0, rest_route_count: 0 };
    const result = storySentence(h(-1.2), peak, empty, fakeT as never);
    expect(result).toBe('overview.story.ahead_short:{"delta":"1.2","days":7}');
  });

  it("falls back to the short template when concentration itself is null", () => {
    const result = storySentence(h(0.2), peak, null, fakeT as never);
    expect(result).toBe('overview.story.flat_short:{"delta":"0.2","days":7}');
  });

  it("caps the concentration clause at the routes the concentration card shows", () => {
    const wide: OverviewConcentration = {
      top_routes: [
        { route_code: "a", route_short_name: null, share_pct: 10 },
        { route_code: "b", route_short_name: null, share_pct: 10 },
        { route_code: "c", route_short_name: null, share_pct: 10 },
        { route_code: "d", route_short_name: null, share_pct: 10 },
        { route_code: "e", route_short_name: null, share_pct: 10 },
        { route_code: "f", route_short_name: null, share_pct: 10 },
      ],
      rest_share_pct: 40,
      rest_route_count: 0,
    };
    const result = storySentence(h(0.9), peak, wide, fakeT as never);
    expect(result).toBe('overview.story.behind:{"delta":"0.9","days":7,"peak":"17:00–18:00","share":50,"count":5}');
  });

  it("counts the comparison window's days from its own dates", () => {
    const result = storySentence(
      { delta_min: 0.9, window_from: "2026-09-27", window_to: "2026-09-29" },
      null,
      null,
      fakeT as never,
    );
    expect(result).toBe('overview.story.behind_short:{"delta":"0.9","days":3}');
  });
});
