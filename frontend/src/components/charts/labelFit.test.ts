// @vitest-environment node
import { describe, expect, it } from "vitest";
import { fitLabel } from "./labelFit";

/** One unit per code point, so a budget reads as a character count. */
const perChar = (s: string) => [...s].length;

describe("fitLabel", () => {
  it("keeps a name that fits whole, with no ellipsis", () => {
    expect(fitLabel("県立中央病院前", 7, perChar)).toBe("県立中央病院前");
    expect(fitLabel("Riverside Station", 40, perChar)).toBe("Riverside Station");
  });

  it("cuts a name that overflows to the longest prefix that fits with an ellipsis", () => {
    const name = "デイサービスセンターふぁみりい尾道トッツ本社前";
    const fitted = fitLabel(name, 8, perChar);
    expect(fitted).toBe("デイサービスセ…");
    expect(perChar(fitted)).toBeLessThanOrEqual(8);
  });

  it("measures the real width rather than counting characters", () => {
    // A narrow Latin letter is half as wide as a CJK one here.
    const width = (s: string) => [...s].reduce((sum, c) => sum + (c.charCodeAt(0) < 0x2000 ? 0.5 : 1), 0);
    expect(fitLabel("Market St.", 5, width)).toBe("Market St.");
    expect(fitLabel("市役所前バス停", 5, width)).toBe("市役所前…");
  });

  it("never splits a character made of two code units", () => {
    expect(fitLabel("𠮷野家前停留所", 3, perChar)).toBe("𠮷野…");
  });

  it("keeps at least one character and the ellipsis when even that overflows", () => {
    expect(fitLabel("本町", 1, perChar)).toBe("本…");
  });
});
