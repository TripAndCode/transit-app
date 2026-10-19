import { describe, expect, it } from "vitest";
import { searchKey } from "./searchText";

describe("searchKey", () => {
  it("folds full-width digits and letters, and case, as an IME types them", () => {
    expect(searchKey("４２")).toBe("42");
    expect(searchKey("ＡＢｃ")).toBe("abc");
  });

  it("folds half-width katakana to full-width", () => {
    expect(searchKey("ｱｵﾓﾘ")).toBe("アオモリ");
  });

  it("trims the surrounding space a query is typed with", () => {
    expect(searchKey("  Ｊ２０ ")).toBe("j20");
  });
});
