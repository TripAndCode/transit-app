// @vitest-environment node
import { describe, it, expect } from "vitest";
import type { Agency } from "../api/types";
import {
  agencyFreshness,
  buildAgencySwitchPath,
  orderAgenciesForSwitcher,
  typeAheadIndex,
} from "./agencySwitcherModel";

function agency(id: number, name: string, latest: string | null = null): Agency {
  return { agency_id: id, agency_name: name, feed_url: "", static_url: null, latest_data_date: latest };
}

const ALL = [agency(1, "Hokuriku Transit"), agency(2, "Kaga Bay Bus"), agency(3, "Noto Rail"), agency(4, "Sea Line")];

describe("orderAgenciesForSwitcher", () => {
  it("lists recent agencies first, most recent first, and the rest in payload order", () => {
    const { recent, others } = orderAgenciesForSwitcher(ALL, [3, 1]);
    expect(recent.map((a) => a.agency_id)).toEqual([3, 1]);
    expect(others.map((a) => a.agency_id)).toEqual([2, 4]);
  });

  it("never repeats an agency across the two groups", () => {
    const { recent, others } = orderAgenciesForSwitcher(ALL, [2]);
    const ids = [...recent, ...others].map((a) => a.agency_id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids).toHaveLength(ALL.length);
  });

  it("drops recent ids that no longer exist in the agencies payload", () => {
    const { recent, others } = orderAgenciesForSwitcher(ALL, [99, 2]);
    expect(recent.map((a) => a.agency_id)).toEqual([2]);
    expect(others.map((a) => a.agency_id)).toEqual([1, 3, 4]);
  });

  it("leaves the recent group empty when nothing has been visited yet", () => {
    const { recent, others } = orderAgenciesForSwitcher(ALL, []);
    expect(recent).toEqual([]);
    expect(others).toHaveLength(4);
  });
});

describe("buildAgencySwitchPath", () => {
  it("keeps the current tab segment and the filter query suffix", () => {
    expect(buildAgencySwitchPath(7, "reports", "?from=2026-06-01&to=2026-06-07")).toBe(
      "/agencies/7/reports?from=2026-06-01&to=2026-06-07",
    );
  });

  it("falls back to the operations tab when the location carries no tab segment", () => {
    expect(buildAgencySwitchPath(7, undefined, "")).toBe("/agencies/7/operations");
  });
});

describe("agencyFreshness", () => {
  const now = new Date("2026-06-10T09:00:00+09:00");

  it("reports today's data as current", () => {
    expect(agencyFreshness("2026-06-10", now)).toEqual({ level: "current", days: 0 });
  });

  it("still reports yesterday's data as current, since a daily feed lands a day behind", () => {
    expect(agencyFreshness("2026-06-09", now)).toEqual({ level: "current", days: 1 });
  });

  it("reports data within the last week as recent", () => {
    expect(agencyFreshness("2026-06-05", now)).toEqual({ level: "recent", days: 5 });
  });

  it("reports older data as stale", () => {
    expect(agencyFreshness("2026-05-01", now)).toEqual({ level: "stale", days: 40 });
  });

  it("reports a missing or unparseable date as unknown", () => {
    expect(agencyFreshness(null, now)).toEqual({ level: "unknown", days: null });
    expect(agencyFreshness("not-a-date", now)).toEqual({ level: "unknown", days: null });
  });
});

describe("typeAheadIndex", () => {
  const names = ALL.map((a) => a.agency_name);

  it("selects the first name starting with the typed buffer, case-insensitively", () => {
    expect(typeAheadIndex(names, "ka", 0)).toBe(1);
    expect(typeAheadIndex(names, "SEA", 0)).toBe(3);
  });

  it("falls back to a substring match when nothing starts with the buffer", () => {
    expect(typeAheadIndex(names, "rail", 0)).toBe(2);
  });

  it("returns the unchanged index when nothing matches", () => {
    expect(typeAheadIndex(names, "zzz", 2)).toBe(2);
  });
});
