import { describe, it, expect } from "vitest";
import { th, td } from "./tableStyles";

describe("th", () => {
  it("defaults to left-aligned, unwidthed and unpinned", () => {
    const style = th();
    expect(style.textAlign).toBe("left");
    expect(style.width).toBeUndefined();
    expect(style.position).toBeUndefined();
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

  it("pins the header with an opaque background when sticky", () => {
    const style = th({ sticky: true });
    expect(style.position).toBe("sticky");
    expect(style.top).toBe(0);
    expect(style.background).toBe("var(--surface-1)");
  });
});

describe("td", () => {
  it("shares the header's horizontal padding so columns line up", () => {
    expect(td().padding).toBe(th().padding);
  });

  it("has the shared cell font size and row rule", () => {
    const style = td();
    expect(style.fontSize).toBe(13);
    expect(style.borderBottom).toBe("1px solid var(--surface-2)");
  });

  it("applies an explicit alignment", () => {
    expect(td({ align: "right" }).textAlign).toBe("right");
  });
});
