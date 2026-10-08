import { describe, it, expect } from "vitest";
import { isTypingTarget } from "./isTypingTarget";

describe("isTypingTarget", () => {
  it("is true for an input", () => {
    expect(isTypingTarget(document.createElement("input"))).toBe(true);
  });

  it("is true for a textarea", () => {
    expect(isTypingTarget(document.createElement("textarea"))).toBe(true);
  });

  it("is true for a select", () => {
    expect(isTypingTarget(document.createElement("select"))).toBe(true);
  });

  it("is true for a contentEditable element", () => {
    // jsdom does not implement `isContentEditable` itself, so it's stubbed
    // directly rather than via the `contentEditable` attribute.
    const div = document.createElement("div");
    Object.defineProperty(div, "isContentEditable", { value: true });
    expect(isTypingTarget(div)).toBe(true);
  });

  it("is falsy for a plain button", () => {
    expect(isTypingTarget(document.createElement("button"))).toBeFalsy();
  });

  it("is false for null", () => {
    expect(isTypingTarget(null)).toBe(false);
  });

  it("is false for a non-HTMLElement EventTarget", () => {
    expect(isTypingTarget(window)).toBe(false);
  });
});
