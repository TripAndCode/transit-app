import { describe, expect, it } from "vitest";
import { timetableChoices } from "./serviceDays";

const THREE_WAY = ["weekday", "saturday", "sunday_holiday"] as const;
const TWO_WAY = ["weekday", "weekend_holiday"] as const;

describe("timetable choices", () => {
  it("offers each of the feed's own buckets", () => {
    expect(timetableChoices("all", THREE_WAY).options).toEqual(["平日", "土曜", "日祝"]); // i18n-ignore: query contract
    expect(timetableChoices("all", TWO_WAY).options).toEqual(["平日", "土日祝"]); // i18n-ignore: query contract
  });

  it("splits three ways until the feed's calendar is known", () => {
    expect(timetableChoices("all").options).toEqual(["平日", "土曜", "日祝"]); // i18n-ignore: query contract
  });

  it("shows an offered value as itself", () => {
    expect(timetableChoices("all", TWO_WAY).shown).toBe("all");
    expect(timetableChoices("平日", TWO_WAY).shown).toBe("平日"); // i18n-ignore: query contract
    expect(timetableChoices("土曜", THREE_WAY).shown).toBe("土曜"); // i18n-ignore: query contract
  });

  it.each(["土曜", "日祝"] as const)("shows %s on a feed split two ways as the 土日祝 it resolves to", (value) => { // i18n-ignore: query contract
    expect(timetableChoices(value, TWO_WAY)).toEqual({ options: ["平日", "土日祝"], shown: "土日祝" }); // i18n-ignore: query contract
  });

  it("keeps a legacy 土日祝 on a feed split three ways as a choice of its own", () => { // i18n-ignore: query contract
    expect(timetableChoices("土日祝", THREE_WAY)).toEqual({ options: ["平日", "土曜", "日祝", "土日祝"], shown: "土日祝" }); // i18n-ignore: query contract
  });
});
