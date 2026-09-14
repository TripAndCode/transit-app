import { describe, expect, it } from "vitest";
import { design } from "./design";

describe("design translation namespace", () => {
  it("has the same key set in ja and en", () => {
    const jaKeys = Object.keys(design.ja).sort();
    const enKeys = Object.keys(design.en).sort();
    expect(enKeys).toEqual(jaKeys);
  });
});
