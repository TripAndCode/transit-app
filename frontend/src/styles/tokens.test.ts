import { describe, it, expect } from "vitest";
import { HEAT_RAMP } from "./tokens";

describe("HEAT_RAMP", () => {
  it("is an ascending density ramp that starts transparent and ends red", () => {
    const stops = HEAT_RAMP.map(([d]) => d);
    expect(stops).toEqual([...stops].sort((a, b) => a - b)); // ascending
    expect(stops[0]).toBe(0);
    expect(HEAT_RAMP[0][1]).toBe("rgba(124,58,237,0)"); // transparent low end
    expect(HEAT_RAMP[HEAT_RAMP.length - 1]).toEqual([1, "#d92121"]); // red core
  });
  it("avoids green/tan/blue so the low end survives any basemap", () => {
    expect(HEAT_RAMP[1][1]).toBe("rgba(124,58,237,0.5)");
  });
});
