// @vitest-environment node
import { describe, expect, it } from "vitest";
import ON_TIME from "./onTimeDefault.json";
import { DEFAULT_WINDOW, EARLY_ANY, PRESET_NAMES, earlyParam, isOnTime, scopeWindow } from "./onTimeDefault";

// tests/unit/test_on_time_default.py pins onTimeDefault.json to the server's
// DEFAULT_PRESET, ON_TIME_PRESETS and EARLY_ANY; this pins the module to the
// file, so the browser and the server cannot name different defaults.
describe("the on-time default", () => {
  it("is the file's default preset, the industry window", () => {
    expect(ON_TIME.default_preset).toBe("standard_m1_p5");
    expect(DEFAULT_WINDOW).toEqual(ON_TIME.presets.standard_m1_p5);
    expect(DEFAULT_WINDOW).toEqual({ early_sec: 60, late_sec: 300 });
    expect(EARLY_ANY).toBe(ON_TIME.early_any);
    expect([...PRESET_NAMES]).toEqual(Object.keys(ON_TIME.presets));
  });

  it("fills each tolerance the scope leaves out from the default", () => {
    expect(scopeWindow({ early: null, late: null })).toEqual({ early_sec: 60, late_sec: 300 });
    expect(scopeWindow({ early: EARLY_ANY, late: 60 })).toEqual(ON_TIME.presets.legacy_60s);
    expect(scopeWindow({ early: null, late: 180 })).toEqual({ early_sec: 60, late_sec: 180 });
    expect(scopeWindow({ early: 120, late: null })).toEqual({ early_sec: 120, late_sec: 300 });
  });

  it("writes an early bound equal to the default's as no param, and none as 'any'", () => {
    expect(earlyParam(60)).toBeNull();
    expect(earlyParam(null)).toBe(EARLY_ANY);
    expect(earlyParam(120)).toBe(120);
  });

  it("counts a delay on time inside the window, both edges included, as the server does", () => {
    const standard = { early_sec: 60, late_sec: 300 };
    expect([-61, -60, 0, 300, 301].map((d) => isOnTime(d, standard))).toEqual([false, true, true, true, false]);
    const anyEarly = { early_sec: null, late_sec: 60 };
    expect([-3600, 60, 61].map((d) => isOnTime(d, anyEarly))).toEqual([true, true, false]);
  });
});
