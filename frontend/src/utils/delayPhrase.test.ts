import { describe, it, expect } from "vitest";
import i18n from "../i18n";
import { avgDelayText } from "./delayPhrase";

void i18n.changeLanguage("en");
const t = i18n.getFixedT("en");

describe("avgDelayText", () => {
  it("says a positive average is late", () => {
    expect(avgDelayText(t, 3.44)).toBe("avg 3.4 min late");
  });

  it("says a negative average is early, without a minus sign", () => {
    expect(avgDelayText(t, -0.4)).toBe("avg 0.4 min early");
  });

  it("calls an average that rounds to zero late, not early", () => {
    expect(avgDelayText(t, -0.04)).toBe("avg 0.0 min late");
  });
});
