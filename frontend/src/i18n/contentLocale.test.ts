import { describe, expect, it } from "vitest";
import { contentLocale } from "./contentLocale";

describe("contentLocale", () => {
  it.each([
    [{ resolvedLanguage: "en", language: "en" }, "en"],
    [{ resolvedLanguage: "en-US", language: "en-US" }, "en"],
    [{ resolvedLanguage: "ja", language: "ja" }, "ja"],
    [{ resolvedLanguage: undefined, language: "en-GB" }, "en"],
    [{ resolvedLanguage: undefined, language: "fr" }, "ja"],
    [{ resolvedLanguage: undefined, language: undefined as unknown as string }, "ja"],
  ])("reads %o as %s", (instance, expected) => {
    expect(contentLocale(instance)).toBe(expected);
  });
});
