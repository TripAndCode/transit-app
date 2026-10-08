import { describe, it, expect, afterEach, vi } from "vitest";
import i18n from "../i18n";
import {
  formatNumber,
  formatDateTime,
  formatReportTime,
  formatDate,
  formatDateRange,
  formatDuration,
  formatHourRange,
  formatMinutes,
  formatShortDate,
  FILTER_SEPARATOR,
  EM_DASH,
  fmtPct,
  fmtRatioPct,
} from "./format";

describe("formatNumber", () => {
  afterEach(async () => {
    // The i18n instance is a shared singleton across tests -- reset it to
    // the suite's baseline so a language switch here doesn't leak into
    // another test file.
    await i18n.changeLanguage("en");
  });

  it("groups thousands with a comma in English", async () => {
    await i18n.changeLanguage("en");
    expect(formatNumber(1234567)).toBe("1,234,567");
  });

  it("groups thousands with a comma in Japanese too (Intl ja-JP uses Arabic digits + comma grouping)", async () => {
    await i18n.changeLanguage("ja");
    expect(formatNumber(1234567)).toBe("1,234,567");
  });

  it("passes through Intl.NumberFormatOptions", async () => {
    await i18n.changeLanguage("en");
    expect(formatNumber(0.4567, { style: "percent" })).toBe("46%");
  });
});

describe("formatDateTime", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("renders an English-locale date/time string", async () => {
    await i18n.changeLanguage("en");
    const result = formatDateTime("2026-01-15T09:30:00Z", { dateStyle: "short", timeStyle: undefined });
    expect(result).toMatch(/1\/15\/26|2026/);
  });

  it("renders a Japanese-locale date/time string distinct from English", async () => {
    const iso = "2026-01-15T09:30:00Z";
    await i18n.changeLanguage("en");
    const en = formatDateTime(iso, { dateStyle: "long" });
    await i18n.changeLanguage("ja");
    const ja = formatDateTime(iso, { dateStyle: "long" });
    expect(ja).not.toBe(en);
  });

  it("returns a formatted string for an invalid date instead of throwing", async () => {
    await i18n.changeLanguage("en");
    expect(() => formatDateTime("not-a-date")).not.toThrow();
  });

  it("includes a time component by default (matches the legacy toLocaleString() behavior it replaces)", async () => {
    await i18n.changeLanguage("en");
    const result = formatDateTime("2026-01-15T09:30:00Z");
    expect(result).toMatch(/\d{1,2}:\d{2}/);
  });
});

describe("FILTER_SEPARATOR", () => {
  it("is a locale-neutral middle dot with surrounding spaces", () => {
    expect(FILTER_SEPARATOR).toBe(" · ");
  });
});

describe("EM_DASH", () => {
  it("is a single em dash", () => {
    expect(EM_DASH).toBe("—");
  });
});

describe("fmtPct", () => {
  const noopT = ((key: string) => key) as unknown as Parameters<typeof fmtPct>[1];

  it("formats a value already on a 0-100 scale, without rescaling", () => {
    expect(fmtPct(42.5, noopT)).toBe("42.5%");
  });

  it("returns the em dash for null/non-finite input", () => {
    expect(fmtPct(null, noopT)).toBe(EM_DASH);
    expect(fmtPct(NaN, noopT)).toBe(EM_DASH);
  });
});

describe("fmtRatioPct", () => {
  it("scales a 0..1 ratio up to a percentage", () => {
    expect(fmtRatioPct(0.5)).toBe("50.0%");
  });

  it("returns the em dash for null", () => {
    expect(fmtRatioPct(null)).toBe(EM_DASH);
  });
});

describe("formatMinutes", () => {
  afterEach(async () => i18n.changeLanguage("en"));

  it("spaces the unit as each language does, at one decimal", async () => {
    await i18n.changeLanguage("en");
    expect(formatMinutes(6.34)).toBe("6.3 min");
    expect(formatMinutes(1234.56)).toBe("1,234.6 min");
    await i18n.changeLanguage("ja");
    expect(formatMinutes(6.34)).toBe("6.3分");
  });

  it("returns the em dash for a missing value", () => {
    expect(formatMinutes(null)).toBe("—");
    expect(formatMinutes(Number.NaN)).toBe("—");
  });
});

describe("formatDuration", () => {
  afterEach(async () => i18n.changeLanguage("en"));

  it("reads a delay in seconds as minutes and seconds", async () => {
    await i18n.changeLanguage("en");
    expect(formatDuration(355)).toBe("5 min 55 s");
    expect(formatDuration(42)).toBe("42 s");
    expect(formatDuration(120)).toBe("2 min");
    await i18n.changeLanguage("ja");
    expect(formatDuration(355)).toBe("5分55秒");
  });

  it("returns the em dash for a missing value", () => {
    expect(formatDuration(null)).toBe("—");
  });
});

describe("formatDateRange", () => {
  afterEach(async () => i18n.changeLanguage("en"));

  it("writes the year once, where each language writes it", async () => {
    await i18n.changeLanguage("en");
    expect(formatDateRange("2026-09-02", "2026-10-01")).toBe("Sep 2 – Oct 1, 2026");
    expect(formatDateRange("2026-12-30", "2027-01-03")).toBe("Dec 30, 2026 – Jan 3, 2027");
    await i18n.changeLanguage("ja");
    expect(formatDateRange("2026-09-02", "2026-10-01")).toBe("2026年9月2日〜10月1日");
  });

  it("can leave the year out, and shows one day once", async () => {
    await i18n.changeLanguage("en");
    expect(formatDateRange("2026-09-02", "2026-10-01", { year: false })).toBe("Sep 2 – Oct 1");
    // Across a year boundary the years are what tell the ends apart.
    expect(formatDateRange("2026-12-30", "2027-01-03", { year: false })).toBe("Dec 30, 2026 – Jan 3, 2027");
    expect(formatDateRange("2026-09-02", "2026-09-02")).toBe("Sep 2, 2026");
  });

  it("returns the em dash for an unparseable end", () => {
    expect(formatDateRange("nope", "2026-10-01")).toBe("—");
  });
});

describe("formatDate and formatShortDate", () => {
  afterEach(async () => i18n.changeLanguage("en"));

  it("formats one day in the language's style, and a chart tick as month/day", async () => {
    await i18n.changeLanguage("en");
    expect(formatDate("2026-09-29")).toBe("Sep 29, 2026");
    expect(formatShortDate("2026-09-28")).toBe("9/28");
    await i18n.changeLanguage("ja");
    expect(formatDate("2026-09-29")).toBe("2026年9月29日");
  });

  it("names the weekday on request, the way each language writes it", async () => {
    await i18n.changeLanguage("en");
    expect(formatDate("2026-09-29", { year: false, weekday: true })).toBe("Tue, Sep 29");
    await i18n.changeLanguage("ja");
    expect(formatDate("2026-09-29", { year: false, weekday: true })).toBe("9月29日(火)");
  });
});

describe("formatReportTime", () => {
  afterEach(() => vi.useRealTimers());

  it("gives a report from today its time alone, and an older one its date too", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-03T13:24:00Z")); // 22:24 JST
    expect(formatReportTime("2026-10-03T12:41:00Z")).toBe(formatDateTime("2026-10-03T12:41:00Z", { timeStyle: "short" }));
    expect(formatReportTime("2026-09-29T00:59:00Z")).toBe(formatDateTime("2026-09-29T00:59:00Z"));
  });
});

describe("formatHourRange", () => {
  it("writes an hour as a clock range", () => {
    expect(formatHourRange(17)).toBe("17:00–18:00");
    expect(formatHourRange(9)).toBe("09:00–10:00");
  });
});

describe("formatter reuse", () => {
  afterEach(async () => {
    vi.restoreAllMocks();
    await i18n.changeLanguage("en");
  });

  it("builds one Intl formatter per language and options, not one per value", async () => {
    await i18n.changeLanguage("en");
    const numbers = vi.spyOn(Intl, "NumberFormat");
    const dates = vi.spyOn(Intl, "DateTimeFormat");
    for (let i = 0; i < 50; i++) {
      formatNumber(1000 + i);
      formatDate("2026-09-29");
    }
    expect(numbers.mock.calls.length).toBeLessThanOrEqual(1);
    expect(dates.mock.calls.length).toBeLessThanOrEqual(1);
    await i18n.changeLanguage("ja");
    expect(formatNumber(1234)).toBe("1,234");
    expect(formatDate("2026-09-29")).toBe("2026年9月29日");
  });
});
