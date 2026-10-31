import { describe, expect, it } from "vitest";
import { FEED_HEALTH_BANNER_MIN_SHARE, feedHealthBannerDue } from "./feedHealth";

describe("feedHealthBannerDue", () => {
  it("draws the line at a thousandth of the readings", () => {
    expect(FEED_HEALTH_BANNER_MIN_SHARE).toBe(0.001);
  });

  it("stays quiet below the line, however many readings that is", () => {
    expect(feedHealthBannerDue(19_345_155, 112)).toBe(false);
    expect(feedHealthBannerDue(100_000, 99)).toBe(false);
  });

  it("speaks up at the line and above it", () => {
    expect(feedHealthBannerDue(100_000, 100)).toBe(true);
    expect(feedHealthBannerDue(1_011_299, 2_321)).toBe(true);
  });

  it("stays quiet when nothing was set aside", () => {
    expect(feedHealthBannerDue(100_000, 0)).toBe(false);
    expect(feedHealthBannerDue(0, 0)).toBe(false);
  });

  it("speaks up when readings were set aside but their total is unknown, since the share cannot be judged small", () => {
    expect(feedHealthBannerDue(0, 5)).toBe(true);
  });
});
