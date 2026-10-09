import { describe, it, expect, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { readBoolPref, useBoolPref, writeBoolPref } from "./mapLayerPrefs";

beforeEach(() => localStorage.clear());

describe("mapLayerPrefs", () => {
  it("falls back when nothing is stored, and round-trips a write", () => {
    expect(readBoolPref("transit.test", true)).toBe(true);
    writeBoolPref("transit.test", false);
    expect(readBoolPref("transit.test", true)).toBe(false);
  });
  it("useBoolPref seeds from storage and persists the setter's value", () => {
    const { result } = renderHook(() => useBoolPref("transit.test", false));
    expect(result.current[0]).toBe(false);
    act(() => result.current[1](true));
    expect(result.current[0]).toBe(true);
    expect(localStorage.getItem("transit.test")).toBe("1");
  });
});
