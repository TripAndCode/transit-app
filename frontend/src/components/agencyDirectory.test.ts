import { describe, expect, it } from "vitest";
import type { Agency } from "../api/types";
import { groupAgencies, hasStopped, newestDataDate, prefectureOf } from "./agencyDirectory";

const HIROSHIMA_FEED = "https://ajt-mobusta-gtfs.mcapps.jp/realtime/9/trip_updates.bin";
const AOMORI_FEED = "https://aomoricitybus.com/TripUpdate.pb";

function agency(agency_id: number, agency_name: string, over: Partial<Agency> = {}): Agency {
  return { agency_id, agency_name, feed_url: HIROSHIMA_FEED, static_url: null, latest_data_date: null, ...over };
}

const names = (list: Agency[]) => list.map((a) => a.agency_name);

describe("prefectureOf", () => {
  it("reads the prefecture from the source that publishes the agency's feed", () => {
    expect(prefectureOf(agency(9, "Hiroshima Bus"))).toBe("prefectures.hiroshima");
    expect(prefectureOf(agency(1, "Aomori City Bus", { feed_url: AOMORI_FEED }))).toBe("prefectures.aomori");
  });

  it("reads Aomori from ODPT's mirror of Aomori City Bus's own dataset", () => {
    const feed_url = "https://api-public.odpt.org/api/v4/gtfs/realtime/odpt_AomoriCity_AllLines_trip_update";
    expect(prefectureOf(agency(1, "Aomori City Bus", { feed_url }))).toBe("prefectures.aomori");
  });

  it.each([
    "",
    "not a url",
    "https://example.com/feed.pb",
    // ODPT mirrors operators nationwide; only Aomori's dataset is Aomori.
    "https://api-public.odpt.org/api/v4/gtfs/realtime/odpt_SomeOtherCity_AllLines_trip_update",
    "https://aomoricitybus.com.example.net/TripUpdate.pb",
  ])("knows no prefecture for the feed %j", (feed_url) => {
    expect(prefectureOf(agency(2, "Unknown", { feed_url }))).toBeNull();
  });
});

describe("newestDataDate", () => {
  it("is the latest day any agency has data for", () => {
    const list = [
      agency(1, "A", { latest_data_date: "2026-10-14" }),
      agency(2, "B"),
      agency(3, "C", { latest_data_date: "2026-10-23" }),
    ];
    expect(newestDataDate(list)).toBe("2026-10-23");
  });

  it("is null while no agency has data", () => {
    expect(newestDataDate([agency(1, "A")])).toBeNull();
  });
});

describe("hasStopped", () => {
  it.each([
    ["2026-10-23", false],
    ["2026-10-22", false],
    ["2026-10-21", false],
    ["2026-10-20", true],
    ["2026-10-14", true],
  ])("an agency whose data ends %s, against the newest 2026-10-23, stopped: %s", (latest, stopped) => {
    expect(hasStopped(latest, "2026-10-23")).toBe(stopped);
  });

  it("counts calendar days across a month end", () => {
    expect(hasStopped("2026-09-28", "2026-10-01")).toBe(true);
    expect(hasStopped("2026-09-29", "2026-10-01")).toBe(false);
  });

  it("never calls an agency without data stopped", () => {
    expect(hasStopped(null, "2026-10-23")).toBe(false);
  });
});

describe("groupAgencies", () => {
  it("puts the prefecture with more agencies first, and agencies of no known prefecture last", () => {
    const groups = groupAgencies(
      [
        agency(1, "Aomori City Bus", { feed_url: AOMORI_FEED }),
        agency(2, "Unknown", { feed_url: "" }),
        agency(8, "Hiroden"),
        agency(9, "Hiroshima Bus"),
      ],
      null,
    );
    expect(groups.map((g) => g.prefectureKey)).toEqual(["prefectures.hiroshima", "prefectures.aomori", null]);
    expect(groups.map((g) => names(g.agencies))).toEqual([["Hiroden", "Hiroshima Bus"], ["Aomori City Bus"], ["Unknown"]]);
  });

  it("orders agency names the Japanese way, kana before kanji", () => {
    const groups = groupAgencies([agency(1, "広島バス"), agency(2, "おのみちバス"), agency(3, "JRバス中国")], null);
    expect(names(groups[0].agencies)).toEqual(["JRバス中国", "おのみちバス", "広島バス"]);
  });

  it("orders a group by name, current data before stopped data before none", () => {
    const groups = groupAgencies(
      [
        agency(1, "Delta", { latest_data_date: "2026-10-14" }),
        agency(2, "Echo"),
        agency(3, "Charlie", { latest_data_date: "2026-10-23" }),
        agency(4, "Alpha"),
        agency(5, "Bravo", { latest_data_date: "2026-10-22" }),
      ],
      "2026-10-23",
    );
    expect(names(groups[0].agencies)).toEqual(["Bravo", "Charlie", "Delta", "Alpha", "Echo"]);
  });
});
