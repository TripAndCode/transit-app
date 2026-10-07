import { describe, it, expect } from "vitest";
import { dimRules, STATIC_DIM_RULES } from "./trendFocusRules";
import { DIM_OPACITY } from "./trendFocus";

function selectors(css: string): string[] {
  return css.split("}").map((r) => r.split("{")[0].trim()).filter(Boolean);
}

describe("dimRules", () => {
  it("emits 21 static weekday rules: three sources times seven ISO weekdays", () => {
    const sels = selectors(STATIC_DIM_RULES);
    expect(sels).toHaveLength(21);
    for (const source of ["daily", "hourly", "dow"]) {
      for (let dow = 1; dow <= 7; dow++) {
        expect(sels).toContain(
          `.trend-focus[data-focus-source="${source}"][data-focus-dow="${dow}"] ` +
            `[data-focus-viewer]:not([data-focus-viewer="${source}"]) ` +
            `[data-mark-dow]:not([data-mark-dow="${dow}"])`,
        );
      }
    }
  });

  it("sets only --focus-dim, to the shared dim opacity", () => {
    const bodies = STATIC_DIM_RULES.split("{").slice(1).map((b) => b.split("}")[0].trim());
    expect(new Set(bodies)).toEqual(new Set([`--focus-dim: ${DIM_OPACITY};`]));
  });

  it("adds one date rule for a focused date, scoped to the other charts", () => {
    const css = dimRules({ source: "daily", date: "2026-05-18", dow: 1 });
    expect(selectors(css)).toHaveLength(22);
    expect(css).toContain(
      `.trend-focus[data-focus-source="daily"] [data-focus-viewer]:not([data-focus-viewer="daily"]) ` +
        `[data-mark-date]:not([data-mark-date="2026-05-18"])`,
    );
  });

  it("emits only the static rules without a focus or without a date", () => {
    expect(dimRules(null)).toBe(STATIC_DIM_RULES);
    expect(dimRules({ source: "dow", dow: 3 })).toBe(STATIC_DIM_RULES);
  });

  it("emits no date rule for a malformed date", () => {
    expect(dimRules({ source: "daily", date: '2026-05-18"] *{display:none}', dow: 1 })).toBe(STATIC_DIM_RULES);
  });
});
