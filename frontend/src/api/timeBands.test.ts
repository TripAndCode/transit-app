import { describe, expect, it } from "vitest";
import i18n from "../i18n";
import { BAND_ORDER, TIME_BAND_HOURS, bandHours, bandLabel, bandOf } from "./timeBands";

const pad = (h: number) => String(h).padStart(2, "0");

describe("TIME_BAND_HOURS", () => {
  it("covers every hour of the day once, in clock order", () => {
    const hours = TIME_BAND_HOURS.flatMap((b) => Array.from({ length: b.to - b.from }, (_, i) => b.from + i));
    expect(hours).toEqual(Array.from({ length: 24 }, (_, h) => h));
  });

  it("puts each hour in the band whose hours hold it", () => {
    expect(bandOf(0)).toBe("late_night");
    expect(bandOf(4)).toBe("late_night");
    expect(bandOf(5)).toBe("morning");
    expect(bandOf(16)).toBe("afternoon");
    expect(bandOf(17)).toBe("evening");
    expect(bandOf(19)).toBe("evening");
    expect(bandOf(20)).toBe("night");
    expect(bandOf(23)).toBe("night");
    expect(BAND_ORDER).toEqual(TIME_BAND_HOURS.map((b) => b.band));
  });

  it.each(["ja", "en"])("agrees with the hours the %s time-band filter names", (lng) => {
    const t = i18n.getFixedT(lng);
    for (const band of BAND_ORDER) {
      const { from, to } = bandHours(band);
      expect(t(`filters.time_band.${band}`)).toContain(`${pad(from)}`);
      expect(t(`filters.time_band.${band}`)).toContain(`${pad(to)}`);
    }
  });

  it("names a band with its hours, so a sentence says which hours it means", () => {
    expect(bandLabel(i18n.getFixedT("ja"), "evening")).toBe("夕 17〜20時"); // i18n-ignore: asserting the ja copy
    expect(bandLabel(i18n.getFixedT("en"), "evening")).toBe("Evening 17–20h");
  });
});
