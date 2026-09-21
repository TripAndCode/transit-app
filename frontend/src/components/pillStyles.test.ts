import { describe, it, expect } from "vitest";
import { pill, groupLabel } from "./pillStyles";

describe("pill", () => {
  it("uses the accent colors when active", () => {
    const style = pill(true);
    expect(style.color).toBe("var(--accent)");
    expect(style.fontWeight).toBe(600);
  });

  it("uses the surface colors when inactive", () => {
    const style = pill(false);
    expect(style.color).toBe("var(--text-secondary)");
    expect(style.fontWeight).toBe(400);
  });

  it("defaults to the standard (md) padding", () => {
    expect(pill(false).padding).toBe("5px 12px");
  });

  it("uses tighter padding for the sm size", () => {
    expect(pill(false, "sm").padding).toBe("4px 12px");
  });
});

describe("groupLabel", () => {
  it("is a small uppercase label style", () => {
    expect(groupLabel.textTransform).toBe("uppercase");
    expect(groupLabel.fontSize).toBe("var(--text-xs)");
  });
});
