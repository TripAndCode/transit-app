import { describe, it, expect } from "vitest";
import i18n from "../i18n";
import { avgDelayText, forecastDelayText } from "./delayPhrase";

void i18n.changeLanguage("en");
const t = i18n.getFixedT("en");

describe("avgDelayText", () => {
  it("says a positive average is late", () => {
    expect(avgDelayText(t, 3.44)).toBe("avg 3.4 min late");
  });

  it("says a negative average is early, without a minus sign", () => {
    expect(avgDelayText(t, -0.4)).toBe("avg 0.4 min early");
  });

  it("rounds a negative half away from zero, as every other delay figure does", () => {
    expect(avgDelayText(t, -1.25)).toBe("avg 1.3 min early");
  });

  it("calls an average that rounds to zero late, not early", () => {
    expect(avgDelayText(t, -0.04)).toBe("avg 0.0 min late");
  });
});

describe("forecastDelayText", () => {
  it("words a forecast model's figure as expected, not as an average", () => {
    expect(forecastDelayText(t, 3.44, "ml")).toBe("3.4 min late expected");
    expect(forecastDelayText(t, -0.4, "ml")).toBe("0.4 min early expected");
  });

  it("keeps a figure from past measurements an average", () => {
    expect(forecastDelayText(t, 3.44, "baseline")).toBe("avg 3.4 min late");
    expect(forecastDelayText(t, -0.4, "baseline")).toBe("avg 0.4 min early");
  });

  it("says the same in Japanese", () => {
    const ja = i18n.getFixedT("ja");
    expect(forecastDelayText(ja, 3.44, "ml")).toBe("予測3.4分の遅れ");
    expect(forecastDelayText(ja, -0.4, "ml")).toBe("予測0.4分の早発");
    expect(forecastDelayText(ja, 3.44, "baseline")).toBe("平均3.4分の遅れ");
  });
});
