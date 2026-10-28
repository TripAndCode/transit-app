import { describe, expect, it } from "vitest";
import i18n from "../i18n";
import { forecastRangeText } from "./forecastRange";

const en = i18n.getFixedT("en");
const ja = i18n.getFixedT("ja");

describe("forecastRangeText", () => {
  it("states the range that holds about 8 runs in 10", () => {
    expect(forecastRangeText(en, 5.8, 1.96, 11.04)).toBe("8 runs in 10 within 2.0–11.0 min late");
    expect(forecastRangeText(ja, 5.8, 1.96, 11.04)).toBe("10回中8回は2.0〜11.0分の遅れの範囲"); // i18n-ignore: asserting the ja copy
  });

  it("reads a lower bound below zero as early running, never as a negative delay", () => {
    expect(forecastRangeText(en, 0.9, -0.4, 3.1)).toBe("8 runs in 10 within 0.4 min early to 3.1 min late");
    expect(forecastRangeText(ja, 0.9, -0.4, 3.1)).toBe("10回中8回は0.4分の早発〜3.1分の遅れの範囲"); // i18n-ignore: asserting the ja copy
    expect(forecastRangeText(en, -1.0, -2.0, -0.5)).toBe("8 runs in 10 within 0.5–2.0 min early");
  });

  it("gives no range where the point falls outside it, since the two would contradict each other", () => {
    expect(forecastRangeText(en, 5.8, 6.0, 11.0)).toBeNull();
    expect(forecastRangeText(en, 12.0, 6.0, 11.0)).toBeNull();
  });

  it("gives no range for a cell without one", () => {
    expect(forecastRangeText(en, 3.0, null, 4.0)).toBeNull();
    expect(forecastRangeText(en, 3.0, undefined, undefined)).toBeNull();
  });
});
