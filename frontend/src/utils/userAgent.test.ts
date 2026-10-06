import { describe, it, expect } from "vitest";
import { describeUserAgent } from "./userAgent";

const MAC_CHROME = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36";
const WIN_EDGE = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0";
const IPHONE_SAFARI = "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Mobile/15E148 Safari/604.1";
const ANDROID_FIREFOX = "Mozilla/5.0 (Android 15; Mobile; rv:140.0) Gecko/140.0 Firefox/140.0";

describe("describeUserAgent", () => {
  it.each([
    [MAC_CHROME, { browser: "Chrome", os: "macOS" }],
    [WIN_EDGE, { browser: "Edge", os: "Windows" }],
    [IPHONE_SAFARI, { browser: "Safari", os: "iOS" }],
    [ANDROID_FIREFOX, { browser: "Firefox", os: "Android" }],
  ])("names the browser and system of %s", (ua, expected) => {
    expect(describeUserAgent(ua)).toEqual(expected);
  });

  it("gives nothing for a string it can't read", () => {
    expect(describeUserAgent("test-ua")).toBeNull();
    expect(describeUserAgent(null)).toBeNull();
  });
});
