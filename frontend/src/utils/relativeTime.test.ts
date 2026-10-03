import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { relativeTime } from "./relativeTime";

const NOW = new Date("2026-10-02T12:00:00Z");
const ago = (seconds: number) => new Date(NOW.getTime() - seconds * 1000).toISOString();

describe("relativeTime", () => {
  afterEach(async () => {
    vi.useRealTimers();
    await i18n.changeLanguage("en");
  });

  it("says just now inside a minute, then counts in words with singular and plural", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    await i18n.changeLanguage("en");
    expect(relativeTime(ago(20))).toBe("just now");
    expect(relativeTime(ago(60))).toBe("1 minute ago");
    expect(relativeTime(ago(12 * 60))).toBe("12 minutes ago");
    expect(relativeTime(ago(3600))).toBe("1 hour ago");
    expect(relativeTime(ago(2 * 86400))).toBe("2 days ago");
    await i18n.changeLanguage("ja");
    expect(relativeTime(ago(20))).toBe("たった今");
    expect(relativeTime(ago(3600))).toBe("1時間前");
  });

  it("returns the em dash for an unparseable or future time", () => {
    expect(relativeTime("nope")).toBe("—");
    expect(relativeTime(new Date(Date.now() + 60_000).toISOString())).toBe("—");
  });
});
