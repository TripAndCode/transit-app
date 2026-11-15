import { describe, it, expect } from "vitest";
import { isHexColor } from "./color";

describe("isHexColor", () => {
  it("accepts a 6-digit hex color, any case", () => {
    expect(isHexColor("#a8391f")).toBe(true);
    expect(isHexColor("#A8391F")).toBe(true);
  });

  it("rejects anything else", () => {
    expect(isHexColor("")).toBe(false);
    expect(isHexColor("a8391f")).toBe(false);
    expect(isHexColor("#fff")).toBe(false);
    expect(isHexColor("rgb(1,2,3)")).toBe(false);
  });
});
