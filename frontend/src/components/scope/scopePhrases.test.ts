import { describe, expect, it } from "vitest";
import i18n from "../../i18n";
import { SCOPE_EXTRAS_NONE, type Scope } from "../../api/scope";
import {
  durationLabel,
  isScopeConditionOff,
  periodDays,
  routeTokenPlates,
  scopeTitle,
  scopeTokens,
  sentenceParts,
  suggestedViewName,
  tokenChanged,
} from "./scopePhrases";

const base: Scope = {
  ...SCOPE_EXTRAS_NONE,
  from: "2026-09-01",
  to: "2026-09-28",
  dow: "all",
  time_band: "all",
  service: "all",
  routes: [],
};

const GROUPS: Record<string, string> = { "61": "K37 観光通り線", "62": "K37 観光通り線", "63": "K37 観光通り線", "70": "A1 国道線" };

function tokens(lng: "ja" | "en", scope: Partial<Scope> = {}) {
  const t = i18n.getFixedT(lng);
  return scopeTokens(
    { ...base, ...scope },
    { t, agencyName: "青森市バス", routeLabel: (c) => `R${c}`, routeGroup: (c) => GROUPS[c] },
  );
}
const labels = (lng: "ja" | "en", scope: Partial<Scope> = {}) => tokens(lng, scope).map((tok) => `${tok.key}=${tok.label}`);

describe("scopeTokens", () => {
  it("says the default scope in words, in both languages", () => {
    expect(labels("ja")).toEqual([
      "agency=青森市バス",
      "routes=全路線",
      "period=9/1〜9/28",
      "days=すべての曜日",
      "time=全時間帯",
      "tolerance=5分以内",
    ]);
    expect(labels("en")).toEqual([
      "agency=青森市バス",
      "routes=all routes",
      "period=9/1 – 9/28",
      "days=every day",
      "time=all hours",
      "tolerance=within 5 min",
    ]);
  });

  it("names one route, counts several, and lists weekdays", () => {
    expect(labels("ja", { routes: ["50"] })).toContain("routes=R50");
    expect(labels("en", { routes: ["50", "51"] })).toContain("routes=2 routes");
    expect(labels("ja", { dow: "mon,wed" })).toContain("days=月・水");
  });

  it("names a line picked by name rather than counting its variants", () => {
    expect(labels("ja", { routes: ["61", "62", "63"] })).toContain("routes=K37 観光通り線 (3系統)");
    expect(labels("en", { routes: ["61", "62", "63"] })).toContain("routes=K37 観光通り線 (3 variants)");
    expect(labels("ja", { routes: ["61", "70"] })).toContain("routes=2路線");
  });

  it("says weekdays and weekends as days, not as timetables", () => {
    expect(labels("ja", { dow: "weekend" })).toContain("days=土日");
    expect(labels("en", { dow: "weekend" })).toContain("days=weekends");
    expect(labels("ja", { dow: "weekday" })).toContain("days=平日");
    expect(labels("en", { dow: "weekday" })).toContain("days=weekdays");
  });

  it("says a single hour as that hour", () => {
    expect(labels("ja", { hour: [7, 7] })).toContain("time=7時台");
  });

  it("shows an hour range in place of the band, mapped to the hour field", () => {
    const time = tokens("ja", { hour: [7, 9] }).find((tok) => tok.key === "time");
    expect(time).toEqual({ key: "time", label: "7時〜9時台", field: "hour" });
  });

  it("adds service, stop and direction only when set, in that order", () => {
    expect(tokens("ja").map((tok) => tok.key)).not.toContain("service");
    expect(labels("ja", { service: "平日", stop: "S1", dir: 0 }).slice(5, 8)).toEqual([
      "service=平日ダイヤ",
      "stop=停留所 S1",
      "dir=方向 0",
    ]);
  });

  it("adds the early tolerance only when set, after direction and before the late tolerance", () => {
    expect(tokens("ja").map((tok) => tok.key)).not.toContain("early");
    const ja = tokens("ja", { dir: 1, early: 120 });
    expect(ja.map((tok) => tok.key).slice(-3)).toEqual(["dir", "early", "tolerance"]);
    expect(ja.find((tok) => tok.key === "early")).toEqual({ key: "early", label: "早発は2分まで", field: "early" });
    expect(labels("en", { early: 30 })).toContain("early=early by up to 30 s");
    expect(labels("ja", { early: "any", late: 60 }).slice(-2)).toEqual(["early=早発も定時扱い", "tolerance=1分以内"]);
    expect(labels("en", { early: "any" })).toContain("early=any early departure on time");
  });

  it("states the tolerance in minutes or seconds", () => {
    expect(labels("ja", { late: 180 })).toContain("tolerance=3分以内");
    expect(labels("en", { late: 90 })).toContain("tolerance=within 1.5 min");
    expect(labels("ja", { late: 30 })).toContain("tolerance=30秒以内");
  });

  it("writes the year when the period crosses one", () => {
    expect(labels("ja", { from: "2025-12-30", to: "2026-01-05" })).toContain("period=2025/12/30〜2026/1/5");
  });

  it("maps each token to the field scope_applied reports", () => {
    expect(
      Object.fromEntries(tokens("ja", { service: "平日", stop: "S1", dir: 1 }).map((tok) => [tok.key, tok.field])),
    ).toEqual({
      agency: null,
      routes: "routes",
      period: "from",
      days: "dow",
      time: "time_band",
      service: "service",
      stop: "stop",
      dir: "dir",
      tolerance: "late",
    });
  });
});

describe("sentenceParts", () => {
  it("splits a template into text and slots", () => {
    expect(sentenceParts("見ているのは[agency]の[routes]を")).toEqual([
      { text: "見ているのは" },
      { slot: "agency" },
      { text: "の" },
      { slot: "routes" },
      { text: "を" },
    ]);
  });
});

describe("scopeTitle", () => {
  it("joins the conditions for a saved-analysis title", () => {
    const t = i18n.getFixedT("ja");
    expect(scopeTitle({ ...base, routes: ["50"] }, { t, agencyName: "x", routeLabel: (c) => `R${c}` })).toBe(
      "R50・9/1〜9/28・すべての曜日・全時間帯・5分以内",
    );
  });
});

describe("periodDays", () => {
  it("counts both ends of the period", () => {
    expect(periodDays(base)).toBe(28);
    expect(periodDays({ from: "2026-09-16", to: "2026-09-16" })).toBe(1);
    expect(periodDays({ from: "2026-12-30", to: "2027-01-02" })).toBe(4);
  });
});

describe("tokenChanged", () => {
  const period = { from: "2026-09-01", to: "2026-09-28" };
  const changed = (scope: Partial<Scope>, p: { from: string; to: string } | null = period) =>
    tokens("en", scope)
      .filter((tok) => tokenChanged(tok.key, { ...base, ...scope }, p))
      .map((tok) => tok.key);

  it("marks nothing at the defaults", () => {
    expect(changed({})).toEqual([]);
  });

  it("marks each condition that differs from what a reset gives back", () => {
    expect(changed({ routes: ["50"], dow: "weekday", time_band: "morning" })).toEqual(["routes", "days", "time"]);
    expect(changed({ hour: [7, 9] })).toEqual(["time"]);
    expect(changed({ from: "2026-09-10" })).toEqual(["period"]);
  });

  it("marks the optional conditions whenever they show, since they show only when set", () => {
    expect(changed({ service: "平日", stop: "S1", dir: 0, early: 60 })).toEqual(["service", "stop", "dir", "early"]);
  });

  it("marks a tolerance only when it differs from the one the server applies by default", () => {
    expect(changed({ late: 300 })).toEqual([]);
    expect(changed({ late: 60 })).toEqual(["tolerance"]);
    expect(changed({ late: 180 })).toEqual(["tolerance"]);
  });

  it("leaves the period unmarked while the default period is unknown", () => {
    expect(changed({ from: "2026-09-10" }, null)).toEqual([]);
  });
});

describe("suggestedViewName", () => {
  const period = { from: "2026-09-01", to: "2026-09-28" };
  const ctx = (lng: "ja" | "en") => ({ t: i18n.getFixedT(lng), agencyName: "x", routeLabel: (c: string) => `R${c}` });

  it("names a view after the conditions it changes", () => {
    expect(suggestedViewName({ ...base, dow: "weekday", time_band: "morning" }, ctx("ja"), period)).toBe(
      "平日・朝(05-09)",
    );
  });

  it("falls back to the routes and the period when nothing is changed", () => {
    expect(suggestedViewName(base, ctx("ja"), period)).toBe("全路線・9/1〜9/28");
    expect(suggestedViewName(base, ctx("en"), period)).toBe("all routes, 9/1 – 9/28");
  });
});

describe("isScopeConditionOff", () => {
  const token = (scope: Partial<Scope>, key: string) => tokens("en", scope).find((tok) => tok.key === key)!;

  it("strikes a set condition the screen's data reports it did not use", () => {
    const scope = { ...base, time_band: "morning" } as Scope;
    expect(isScopeConditionOff(token(scope, "time"), scope, { time_band: false })).toBe(true);
  });

  it("leaves a condition alone when it was used, when nothing is reported, or when it is at its default", () => {
    const scope = { ...base, time_band: "morning" } as Scope;
    expect(isScopeConditionOff(token(scope, "time"), scope, { time_band: true })).toBe(false);
    expect(isScopeConditionOff(token(scope, "time"), scope, undefined)).toBe(false);
    expect(isScopeConditionOff(token(base, "time"), base, { time_band: false })).toBe(false);
    expect(isScopeConditionOff(token(base, "agency"), base, { time_band: false })).toBe(false);
  });
});

describe("routeTokenPlates", () => {
  const PLATES: Record<string, string> = { "50": "50", "51": "51", "61": "K37", "62": "K37", "63": "K38", "70": "A1" };
  const plateCtx = {
    t: i18n.getFixedT("ja"),
    agencyName: "x",
    routeLabel: (c: string) => `R${c}`,
    routeGroup: (c: string) => GROUPS[c],
    routePlate: (c: string) => PLATES[c] ?? null,
  };

  it("shows nothing for no routes picked", () => {
    expect(routeTokenPlates([], plateCtx)).toEqual({ plates: [], more: 0 });
  });

  it("shows one plate for one route, grouped or not", () => {
    expect(routeTokenPlates(["50"], plateCtx)).toEqual({ plates: ["50"], more: 0 });
  });

  it("dedupes a single line's variants that all plate the same, instead of stacking the same plate three times", () => {
    expect(routeTokenPlates(["61", "62"], plateCtx)).toEqual({ plates: ["K37"], more: 0 });
  });

  it("shows a single line's distinct variant plates, up to three", () => {
    expect(routeTokenPlates(["61", "62", "63"], plateCtx)).toEqual({ plates: ["K37", "K38"], more: 0 });
  });

  it("shows one plate per line for a few different lines", () => {
    expect(routeTokenPlates(["50", "61", "70"], plateCtx)).toEqual({ plates: ["50", "K37", "A1"], more: 0 });
  });

  it("folds a line past the third into a +N plate, keeping the first two", () => {
    expect(routeTokenPlates(["50", "51", "61", "70"], plateCtx)).toEqual({ plates: ["50", "51"], more: 2 });
  });

  it("folds a line with no plate of its own into the +N count, never dropping it", () => {
    const ctx = { ...plateCtx, routePlate: (c: string) => (c === "x1" || c === "x2" ? null : (PLATES[c] ?? null)) };
    expect(routeTokenPlates(["50", "70", "x1", "x2"], ctx)).toEqual({ plates: ["50", "A1"], more: 2 });
    expect(routeTokenPlates(["50", "x1"], ctx)).toEqual({ plates: ["50"], more: 1 });
  });

  it("falls back to the route code itself when the context names no plate", () => {
    expect(routeTokenPlates(["50", "70"], { t: i18n.getFixedT("ja"), agencyName: "x", routeLabel: (c) => c })).toEqual({
      plates: ["50", "70"],
      more: 0,
    });
  });
});

describe("durationLabel", () => {
  it("prints a span the way the scope sentence does, seconds under a minute and else minutes to a tenth, its figure held to its unit", () => {
    const en = i18n.getFixedT("en");
    const ja = i18n.getFixedT("ja");
    expect([45, 60, 90, 300].map((sec) => durationLabel(sec, en))).toEqual(["45\u00a0s", "1\u00a0min", "1.5\u00a0min", "5\u00a0min"]);
    expect([45, 60, 90, 300].map((sec) => durationLabel(sec, ja))).toEqual(["45秒", "1分", "1.5分", "5分"]);
  });
});
