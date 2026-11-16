// @vitest-environment node
import { describe, expect, it } from "vitest";
import { csvText } from "./csv";

describe("export", () => {
  it("quotes Japanese text, embedded newlines and formulas without changing negative numbers", () => {
    expect(csvText([["停留所,北", 'a"b\nc', "=1+1", -2, null]])).toBe('\ufeff"停留所,北","a""b\nc","\'=1+1","-2",""');
  });
});
