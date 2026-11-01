import { describe, expect, it } from "vitest";
import { emptyMapReason, serviceDateOf } from "./emptyMapReason";
import type { ServiceHoursResponse } from "../../api/types";

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

describe("emptyMapReason with the timetable's hours for the service day", () => {
  // Service day 10/25 runs from 06:05 to 21:48 JST; 10/26 from 06:05 to 00:27 JST
  // on the 27th, a last trip written 24:27 in the timetable.
  const day25: ServiceHoursResponse = {
    date: "2026-10-25",
    hours: { first_departure_at: "2026-10-24T21:05:00Z", last_arrival_at: "2026-10-25T12:48:00Z" },
  };
  const day26: ServiceHoursResponse = {
    date: "2026-10-26",
    hours: { first_departure_at: "2026-10-25T21:05:00Z", last_arrival_at: "2026-10-26T15:27:00Z" },
  };
  const eveningReport = "2026-10-25T12:40:00Z"; // 21:40 JST on the 25th

  it("says the day's service has ended once the feed has gone quiet after the last trip", () => {
    // 22:30 JST: before midnight, where the fixed night would have blamed the feed.
    expect(emptyMapReason(eveningReport, at("2026-10-25T13:30:00Z"), day25)).toBe("ended");
  });

  it("keeps the ended day through midnight up to the 04:00 cutover", () => {
    expect(emptyMapReason(eveningReport, at("2026-10-25T18:59:00Z"), day25)).toBe("ended"); // 03:59 JST
  });

  it("names the first trip between the cutover and its departure, past the fixed 05:00", () => {
    // 05:30 JST on the 26th, quiet since the evening before.
    expect(emptyMapReason(eveningReport, at("2026-10-25T20:30:00Z"), day26)).toBe("before_first");
  });

  it("calls a feed still quiet ten minutes after the first departure stale, and quiet before then", () => {
    const firstDeparture = at("2026-10-25T21:05:00Z"); // 06:05 JST on the 26th
    expect(emptyMapReason(eveningReport, firstDeparture + 1_000, day26)).toBe("quiet");
    expect(emptyMapReason(eveningReport, firstDeparture + 10 * 60_000, day26)).toBe("quiet");
    expect(emptyMapReason(eveningReport, firstDeparture + 10 * 60_000 + 1_000, day26)).toBe("stale");
  });

  it("still calls a feed quiet during service hours stale", () => {
    // 18:16 JST, last report 18:05 JST.
    expect(emptyMapReason("2026-10-25T09:05:49Z", at("2026-10-25T09:16:49Z"), day25)).toBe("stale");
  });

  it("calls a stall after midnight stale while a late trip is still timetabled", () => {
    // 00:15 JST on the 27th, quiet since 00:00, with the last trip due at 00:27.
    expect(emptyMapReason("2026-10-26T15:00:00Z", at("2026-10-26T15:15:00Z"), day26)).toBe("stale");
    expect(emptyMapReason("2026-10-26T15:00:00Z", at("2026-10-26T15:40:00Z"), day26)).toBe("ended");
  });

  it("names neither the timetable nor a stall while the feed is still reporting", () => {
    // 22:00 JST, after the last trip, with a report five minutes ago.
    expect(emptyMapReason("2026-10-25T12:55:00Z", at("2026-10-25T13:00:00Z"), day25)).toBe("quiet");
    // 05:30 JST, before the first trip, with a report a minute ago.
    expect(emptyMapReason("2026-10-25T20:29:00Z", at("2026-10-25T20:30:00Z"), day26)).toBe("quiet");
  });

  it("does not let the end of service explain a feed quiet since before that service day began", () => {
    const night = at("2026-10-25T13:30:00Z"); // 22:30 JST on the 25th
    expect(emptyMapReason("2026-10-24T18:59:00Z", night, day25)).toBe("stale"); // 03:59 JST: service day 10/24
    expect(emptyMapReason("2026-10-24T19:00:00Z", night, day25)).toBe("ended"); // 04:00 JST opens 10/25
  });

  it("does not let the first trip explain a feed quiet since before the previous service day began", () => {
    const morning = at("2026-10-25T20:30:00Z"); // 05:30 JST on the 26th
    expect(emptyMapReason("2026-10-24T18:59:00Z", morning, day26)).toBe("stale");
    expect(emptyMapReason("2026-10-24T19:00:00Z", morning, day26)).toBe("before_first");
  });

  it("falls back to the fixed night when the hours are for another service day", () => {
    // 05:30 JST on the 26th, but only the 25th's hours have arrived.
    expect(emptyMapReason(eveningReport, at("2026-10-25T20:30:00Z"), day25)).toBe("stale");
    // 02:30 JST on the 26th, still service day 10/25, with the 26th's hours.
    expect(emptyMapReason(eveningReport, at("2026-10-25T17:30:00Z"), day26)).toBe("overnight");
  });

  it("falls back to the fixed night when the timetable runs nothing that day", () => {
    const none: ServiceHoursResponse = { date: "2026-10-25", hours: null };
    expect(emptyMapReason(eveningReport, at("2026-10-25T17:30:00Z"), none)).toBe("overnight"); // 02:30 JST
    expect(emptyMapReason(eveningReport, at("2026-10-25T13:30:00Z"), none)).toBe("stale"); // 22:30 JST
  });

  it("still says nothing has arrived, or claims nothing for an unreadable or future report", () => {
    expect(emptyMapReason(null, at("2026-10-25T13:30:00Z"), day25)).toBe("none");
    expect(emptyMapReason("not a time", at("2026-10-25T13:30:00Z"), day25)).toBe("quiet");
    expect(emptyMapReason("2026-10-25T14:30:00Z", at("2026-10-25T13:30:00Z"), day25)).toBe("quiet");
  });
});

describe("serviceDateOf", () => {
  it("names the JST service date, which turns over at 04:00 JST", () => {
    expect(serviceDateOf(at("2026-10-25T14:59:00Z"))).toBe("2026-10-25"); // 23:59 JST
    expect(serviceDateOf(at("2026-10-25T18:59:00Z"))).toBe("2026-10-25"); // 03:59 JST on the 26th
    expect(serviceDateOf(at("2026-10-25T19:00:00Z"))).toBe("2026-10-26"); // 04:00 JST on the 26th
  });
});
