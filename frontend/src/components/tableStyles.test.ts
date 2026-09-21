import { describe, it, expect } from "vitest";
import { th, td } from "./tableStyles";

describe("th", () => {
  it("defaults to left-aligned, unwidthed", () => {
    const style = th();
    expect(style.textAlign).toBe("left");
    expect(style.width).toBeUndefined();
  });

  it("applies an explicit width", () => {
    expect(th({ width: 40 }).width).toBe(40);
  });

  it("applies an explicit alignment", () => {
    expect(th({ align: "right" }).textAlign).toBe("right");
  });

  it("combines width and alignment", () => {
    const style = th({ width: 40, align: "right" });
    expect(style.width).toBe(40);
    expect(style.textAlign).toBe("right");
  });
});

describe("td", () => {
  it("has the shared cell padding and font size", () => {
    const style = td();
    expect(style.padding).toBe("6px 10px");
    expect(style.fontSize).toBe(13);
  });

  it("applies an explicit alignment", () => {
    expect(td({ align: "right" }).textAlign).toBe("right");
  });
});
