// @vitest-environment node
import { describe, it, expect } from "vitest";
import {
  FLAT_MAX_DELTA_MIN,
  FLAT_MAX_DELTA_PCT,
  latestDayStanding,
  storyVerdict,
  topRoutesShare,
} from "./storySentence";
import type { OverviewConcentration, OverviewServiceSplitDay } from "../../api/types";

function fakeT(key: string, opts?: Record<string, unknown>) {
  return opts ? `${key}:${JSON.stringify(opts)}` : key;
}

/** A seven-day comparison window, the headline's usual one, over a 2.9-minute
 *  window before it. */
function h(delta_min: number | null, delta_pct: number | null, baseline = 2.9) {
  return {
    avg_min: delta_min == null ? baseline : baseline + delta_min,
    baseline_avg_min: baseline,
    delta_min,
    delta_pct,
    window_from: "2026-10-17",
    window_to: "2026-10-23",
  };
}

function branch(delta_min: number, delta_pct: number | null, baseline?: number): string {
  return storyVerdict(h(delta_min, delta_pct, baseline), fakeT as never).sentence.split(":")[0];
}

describe("storyVerdict", () => {
  it("states a longer window with both averages, the change and its share", () => {
    expect(storyVerdict(h(0.32, 11), fakeT as never)).toEqual({
      state: 'overview.story.state_behind:{"count":7,"delta":"0.3"}',
      sentence:
        'overview.story.behind:{"count":7,"current":"3.2","previous":"2.9",' +
        '"change":"overview.story.change_pct:{\\"delta\\":\\"0.3\\",\\"pct\\":11}"}',
    });
  });

  it("states a shorter window the same way", () => {
    expect(storyVerdict(h(-0.58, -19.6, 2.96), fakeT as never)).toEqual({
      state: 'overview.story.state_ahead:{"count":7,"delta":"0.6"}',
      sentence:
        'overview.story.ahead:{"count":7,"current":"2.4","previous":"3.0",' +
        '"change":"overview.story.change_pct:{\\"delta\\":\\"0.6\\",\\"pct\\":20}"}',
    });
  });

  it("calls a window about the same only with both averages beside it", () => {
    expect(storyVerdict(h(0.06, 2.4, 2.46), fakeT as never)).toEqual({
      state: 'overview.story.state_flat:{"count":7}',
      sentence: 'overview.story.flat:{"count":7,"current":"2.5","previous":"2.5"}',
    });
  });

  // "About the same" is a claim: it holds only for a change a reader could not
  // tell from no change, either under the figures' 0.1-minute precision or
  // under 5% of the window before.
  it("reads a change under 0.1 min as about the same, whatever its share", () => {
    expect(FLAT_MAX_DELTA_MIN).toBe(0.1);
    expect(branch(0.09, 30, 0.3)).toBe("overview.story.flat");
    expect(branch(-0.09, -30, 0.3)).toBe("overview.story.flat");
    expect(branch(0.1, 30, 0.33)).toBe("overview.story.behind");
  });

  it("reads a change under 5% as about the same, whatever its minutes", () => {
    expect(FLAT_MAX_DELTA_PCT).toBe(5);
    expect(branch(0.45, 4.9, 9.2)).toBe("overview.story.flat");
    expect(branch(-0.45, -4.9, 9.2)).toBe("overview.story.flat");
    expect(branch(0.46, 5, 9.2)).toBe("overview.story.behind");
    expect(branch(-0.46, -5, 9.2)).toBe("overview.story.ahead");
  });

  it("reads an 11% rise as longer, not about the same", () => {
    expect(branch(0.32, 11)).toBe("overview.story.behind");
  });

  it("reads no change at all as about the same", () => {
    expect(branch(0, 0, 4.63)).toBe("overview.story.flat");
  });

  it("judges by minutes alone when there is no share to compare (a window before at 0 min)", () => {
    expect(branch(0.4, null, 0)).toBe("overview.story.behind");
    expect(branch(0.05, null, 0)).toBe("overview.story.flat");
    expect(storyVerdict(h(0.4, null, 0), fakeT as never).sentence).toContain("overview.story.change:");
  });

  it("counts the comparison window's days from its own dates", () => {
    const verdict = storyVerdict({ ...h(0.9, 30), window_from: "2026-10-21", window_to: "2026-10-23" }, fakeT as never);
    expect(verdict.sentence).toMatch(/^overview\.story\.behind:\{"count":3,/);
  });
});

/** Thirty days of `samples` observations each at 3 min, Monday 9/21 first. */
function month(samples = 1000): OverviewServiceSplitDay[] {
  return Array.from({ length: 30 }, (_, i) => {
    const date = new Date(Date.UTC(2026, 8, 21 + i)).toISOString().slice(0, 10);
    const dow = new Date(`${date}T00:00:00Z`).getUTCDay();
    const day_type = dow === 0 ? "sunday_holiday" : dow === 6 ? "saturday" : "weekday";
    return { date, day_type, avg_min: 3, samples };
  });
}

function withLatest(days: OverviewServiceSplitDay[], patch: Partial<OverviewServiceSplitDay>) {
  return [...days.slice(0, -1), { ...days[days.length - 1], ...patch }];
}

describe("latestDayStanding", () => {
  const latest = "2026-10-20"; // a Tuesday, the 30th day

  it("ranks the latest day among the days observed when it is one of the slowest three", () => {
    const days = withLatest(month(), { avg_min: 5.02 });
    days[3] = { ...days[3], avg_min: 5.32 };
    expect(latestDayStanding(days, latest)).toEqual({ date: latest, avgMin: 5.02, rank: 2, days: 30 });
  });

  it("calls the slowest day first", () => {
    expect(latestDayStanding(withLatest(month(), { avg_min: 4 }), latest)?.rank).toBe(1);
  });

  it("says nothing when the latest day is not among the slowest three", () => {
    const days = withLatest(month(), { avg_min: 4 });
    for (const i of [1, 2, 3]) days[i] = { ...days[i], avg_min: 5 };
    expect(latestDayStanding(days, latest)).toBeNull();
  });

  it("counts only the days observed, not the days in the period", () => {
    const days = withLatest(month(), { avg_min: 4 }).slice(4);
    expect(latestDayStanding(days, latest)?.days).toBe(26);
  });

  it("does not rank a latest day collected only in part", () => {
    // Under half a usual day of its own day type: its average covers only
    // some of the day's hours.
    expect(latestDayStanding(withLatest(month(), { avg_min: 4, samples: 499 }), latest)).toBeNull();
    expect(latestDayStanding(withLatest(month(), { avg_min: 4, samples: 500 }), latest)?.rank).toBe(1);
  });

  it("judges a weekend latest day against weekend days, which run fewer trips", () => {
    const days = month().map((d) => (d.day_type === "weekday" ? d : { ...d, samples: 300 }));
    const sunday = days.findIndex((d) => d.date === "2026-10-18");
    const upToSunday = days.slice(0, sunday + 1);
    upToSunday[sunday] = { ...upToSunday[sunday], avg_min: 4 };
    expect(latestDayStanding(upToSunday, "2026-10-18")?.rank).toBe(1);
  });

  it("says nothing over fewer than a week of days", () => {
    const days = withLatest(month(), { avg_min: 4 }).slice(-6);
    expect(latestDayStanding(days, latest)).toBeNull();
  });

  it("says nothing without enough days of the latest day's type to judge it by", () => {
    const days = withLatest(month(), { avg_min: 4, day_type: "weekend_holiday" });
    expect(latestDayStanding(days, latest)).toBeNull();
  });

  it("says nothing when the headline's last day has no daily row", () => {
    expect(latestDayStanding(month(), "2026-10-21")).toBeNull();
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
