import { describe, it, expect } from "vitest";
import { isPlainLeftClick } from "./clicks";

const click = (over: Partial<{ button: number; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean }> = {}) => ({
  button: 0,
  metaKey: false,
  ctrlKey: false,
  shiftKey: false,
  altKey: false,
  ...over,
});

describe("isPlainLeftClick", () => {
  it("is a same-tab click: the primary button with no modifier", () => {
    expect(isPlainLeftClick(click())).toBe(true);
  });
  it("leaves new-tab, new-window and secondary clicks to the browser", () => {
    for (const over of [{ metaKey: true }, { ctrlKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }]) {
      expect(isPlainLeftClick(click(over))).toBe(false);
    }
  });
});
