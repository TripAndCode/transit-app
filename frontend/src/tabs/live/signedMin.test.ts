import { describe, it, expect } from "vitest";
import { signedMin } from "./signedMin";

function fakeT(key: string, opts?: Record<string, unknown>) {
  return opts ? `${key}:${JSON.stringify(opts)}` : key;
}

describe("signedMin", () => {
  it("prefixes a positive value with a single + sign", () => {
    expect(signedMin(180, fakeT as never)).toBe('common.unit_min_signed:{"sign":"+","value":3}');
  });

  it("prefixes a negative value with a single - sign, never a double sign like '+-'", () => {
    // -90 sec = -1.5 min; Math.round ties toward +Infinity, so this is -1 min, magnitude 1.
    const label = signedMin(-90, fakeT as never);
    expect(label).toBe('common.unit_min_signed:{"sign":"-","value":1}');
    expect(label).not.toContain("+-");
  });

  it("treats zero as non-negative (a leading +)", () => {
    expect(signedMin(0, fakeT as never)).toBe('common.unit_min_signed:{"sign":"+","value":0}');
  });
});
