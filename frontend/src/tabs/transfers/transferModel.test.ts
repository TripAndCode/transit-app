import { describe, expect, it } from "vitest";
import type { TransferPairSummary, TransferTally } from "../../api/types";
import { minutes, outcomeWidths, pickDayType, pickPair } from "./transferModel";

const side = { agency_id: 1, agency_name: "a", route_code: "r", route_name: null, direction: null, stop_ids: ["s"], stop_names: [null] };
const pair = (id: number, day_types: TransferPairSummary["day_types"] = ["weekday", "saturday"]): TransferPairSummary => ({
  id,
  name: `p${id}`,
  arriving: side,
  departing: side,
  walk_sec: 120,
  day_types,
});

describe("transferModel", () => {
  it("picks the requested pair, else the first, else none", () => {
    expect(pickPair([pair(1), pair(2)], "2")?.id).toBe(2);
    expect(pickPair([pair(1), pair(2)], "9")?.id).toBe(1);
    expect(pickPair([], "1")).toBeNull();
  });

  it("keeps a requested day type only when the pair is read on it", () => {
    expect(pickDayType(pair(1), "saturday")).toBe("saturday");
    expect(pickDayType(pair(1), "sunday_holiday")).toBe("weekday");
    expect(pickDayType(pair(1, ["sunday_holiday"]), null)).toBe("sunday_holiday");
  });

  it("splits a bar by outcome over the planned connections, unobserved included", () => {
    const t: TransferTally = { planned: 4, held: 2, missed: 1, unobserved: 1, stranded: 0, held_share: 2 / 3, small_sample: true };
    expect(outcomeWidths(t)).toEqual({ held: 50, missed: 25, unobserved: 25 });
    expect(outcomeWidths({ ...t, planned: 0, held: 0, missed: 0, unobserved: 0 })).toEqual({ held: 0, missed: 0, unobserved: 0 });
  });

  it("rounds seconds to minutes", () => {
    expect(minutes(89)).toBe(1);
    expect(minutes(90)).toBe(2);
  });
});
