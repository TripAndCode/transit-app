import { describe, it, expect, afterEach } from "vitest";
import i18n from "../i18n";
import { formatNumber, formatDateTime, FILTER_SEPARATOR } from "./format";

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
