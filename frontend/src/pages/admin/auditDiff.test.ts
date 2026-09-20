import { describe, it, expect } from "vitest";
import { diffEntries, formatDiffValue } from "./auditDiff";

describe("diffEntries", () => {
  it("returns nothing for two null snapshots (e.g. a merged login event)", () => {
    expect(diffEntries(null, null)).toEqual([]);
  });

  it("marks a field changed between before and after", () => {
    const entries = diffEntries({ role: "user" }, { role: "admin" });
    expect(entries).toEqual([{ key: "role", before: "user", after: "admin", changed: true }]);
  });

  it("marks an unchanged field as changed:false", () => {
    const entries = diffEntries({ role: "admin", suspended: false }, { role: "admin", suspended: true });
    expect(entries).toEqual([
      { key: "role", before: "admin", after: "admin", changed: false },
      { key: "suspended", before: false, after: true, changed: true },
    ]);
  });

  it("sorts keys deterministically regardless of input order", () => {
    const entries = diffEntries({ z: 1, a: 2 }, { z: 1, a: 3 });
    expect(entries.map((e) => e.key)).toEqual(["a", "z"]);
  });

  it("treats a key only present after as before: null", () => {
    const entries = diffEntries(null, { agency_name: "Foo" });
    expect(entries).toEqual([{ key: "agency_name", before: null, after: "Foo", changed: true }]);
  });

  it("treats a key only present before as after: null", () => {
    const entries = diffEntries({ email: "a@b.com" }, null);
    expect(entries).toEqual([{ key: "email", before: "a@b.com", after: null, changed: true }]);
  });
});

describe("formatDiffValue", () => {
  it("renders null as an em dash", () => {
    expect(formatDiffValue(null)).toBe("—");
  });

  it("renders booleans as literal strings", () => {
    expect(formatDiffValue(true)).toBe("true");
    expect(formatDiffValue(false)).toBe("false");
  });

  it("renders strings as-is", () => {
    expect(formatDiffValue("admin")).toBe("admin");
  });

  it("renders numbers via String()", () => {
    expect(formatDiffValue(42)).toBe("42");
  });
});
