import { describe, expect, it } from "vitest";
import { emptyMapReason } from "./emptyMapReason";

// Every instant is written in UTC; JST is UTC+9 all year.
const at = (iso: string) => Date.parse(iso);

describe("emptyMapReason", () => {
  it("says nothing has been received when there is no report at all", () => {
    expect(emptyMapReason(null, at("2026-10-25T09:16:00Z"))).toBe("none");
  });

  it("calls a feed quiet past the live window in the daytime stale, not overnight", () => {
    // 18:16 JST, last report 18:05 JST.
    expect(emptyMapReason("2026-10-25T09:05:49Z", at("2026-10-25T09:16:49Z"))).toBe("stale");
  });

  it("calls an empty map under a fresh feed quiet, without blaming the feed or the hour", () => {
    // 14:00 JST, last report a minute earlier: the route filter left nothing to draw.
    expect(emptyMapReason("2026-10-25T04:59:00Z", at("2026-10-25T05:00:00Z"))).toBe("quiet");
  });

  it("explains a feed that went quiet after the evening's last trips by the night", () => {
    // 02:30 JST, last report 23:40 JST the evening before.
    expect(emptyMapReason("2026-10-25T14:40:00Z", at("2026-10-25T17:30:00Z"))).toBe("overnight");
  });

  it("starts the night at midnight JST and leaves the hour before it to the stale rule", () => {
    expect(emptyMapReason("2026-10-25T14:40:00Z", at("2026-10-25T14:59:00Z"))).toBe("stale");
    expect(emptyMapReason("2026-10-25T14:40:00Z", at("2026-10-25T15:00:00Z"))).toBe("overnight");
  });

  it("holds the night across the 04:00 service-day cutover and ends it at 05:00 JST", () => {
    const lastTrip = "2026-10-25T14:40:00Z";
    expect(emptyMapReason(lastTrip, at("2026-10-25T18:59:00Z"))).toBe("overnight"); // 03:59 JST
    expect(emptyMapReason(lastTrip, at("2026-10-25T19:30:00Z"))).toBe("overnight"); // 04:30 JST
    expect(emptyMapReason(lastTrip, at("2026-10-25T20:00:00Z"))).toBe("stale"); // 05:00 JST
  });

  it("does not let the night explain a feed quiet since before the service day it closes began", () => {
    const night = at("2026-10-25T17:00:00Z"); // 02:00 JST on 10/26
    // 15:00 JST on 10/24: quiet for more than a day.
    expect(emptyMapReason("2026-10-24T06:00:00Z", night)).toBe("stale");
    // 03:59 JST on 10/25 belongs to service day 10/24; 04:00 opens service day 10/25.
    expect(emptyMapReason("2026-10-24T18:59:00Z", night)).toBe("stale");
    expect(emptyMapReason("2026-10-24T19:00:00Z", night)).toBe("overnight");
  });

  it("claims neither a stall nor the night for an unreadable timestamp", () => {
    expect(emptyMapReason("not a time", at("2026-10-25T09:16:00Z"))).toBe("quiet");
  });

  it("claims neither a stall nor the night for a report ahead of the clock, even overnight", () => {
    // 02:30 JST, and the feed is stamped an hour later than the client clock.
    expect(emptyMapReason("2026-10-25T18:30:00Z", at("2026-10-25T17:30:00Z"))).toBe("quiet");
  });
});
