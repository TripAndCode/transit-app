import { describe, expect, it } from "vitest";
import type { TimetableRevision } from "../../api/types";
import { pickRevision, serviceClock } from "./revisionModel";

function revision(first_day: string): TimetableRevision {
  const side = { version: "v", content: "abcd1234", first_day, last_day: first_day };
  return { first_day, before: side, after: side };
}

describe("serviceClock", () => {
  it("writes a departure after midnight past 24:00, as the timetable does", () => {
    expect(serviceClock(21 * 3600 + 10 * 60)).toBe("21:10");
    expect(serviceClock(24 * 3600 + 30 * 60)).toBe("24:30");
    expect(serviceClock(6 * 3600 + 5 * 60)).toBe("6:05");
  });

  it("shows a dash for a stop with no departure", () => {
    expect(serviceClock(null)).toBe("—");
  });
});

describe("pickRevision", () => {
  const list = [revision("2026-10-01"), revision("2026-04-01")];

  it("picks the revision `on` names", () => {
    expect(pickRevision(list, "2026-04-01")?.first_day).toBe("2026-04-01");
  });

  it("falls back to the newest for no `on` or one the list lacks", () => {
    expect(pickRevision(list, null)?.first_day).toBe("2026-10-01");
    expect(pickRevision(list, "2025-01-01")?.first_day).toBe("2026-10-01");
    expect(pickRevision([], null)).toBeNull();
  });
});
