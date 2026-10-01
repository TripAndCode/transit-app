import { describe, expect, it } from "vitest";
import i18n from "../../i18n";
import { SCOPE_EXTRAS_NONE, type Scope } from "../../api/scope";
import { scopeTitle, scopeTokens, sentenceParts } from "./scopePhrases";

const base: Scope = {
  ...SCOPE_EXTRAS_NONE,
  from: "2026-09-01",
  to: "2026-09-28",
  dow: "all",
  time_band: "all",
  service: "all",
  routes: [],
};

function tokens(lng: "ja" | "en", scope: Partial<Scope> = {}) {
  const t = i18n.getFixedT(lng);
  return scopeTokens({ ...base, ...scope }, { t, agencyName: "青森市バス", routeLabel: (c) => `R${c}` });
}
const labels = (lng: "ja" | "en", scope: Partial<Scope> = {}) => tokens(lng, scope).map((tok) => `${tok.key}=${tok.label}`);

describe("scopeTokens", () => {
  it("says the default scope in words, in both languages", () => {
    expect(labels("ja")).toEqual([
      "agency=青森市バス",
      "routes=全路線",
      "period=9/1〜9/28",
      "days=すべての曜日",
      "time=終日",
      "tolerance=1分以内",
    ]);
    expect(labels("en")).toEqual([
      "agency=青森市バス",
      "routes=all routes",
      "period=9/1 – 9/28",
      "days=every day",
      "time=all day",
      "tolerance=within 1 min",
    ]);
  });

  it("names one route, counts several, and lists weekdays", () => {
    expect(labels("ja", { routes: ["50"] })).toContain("routes=R50");
    expect(labels("en", { routes: ["50", "51"] })).toContain("routes=2 routes");
    expect(labels("ja", { dow: "mon,wed" })).toContain("days=月・水");
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
      "R50・9/1〜9/28・すべての曜日・終日・1分以内",
    );
  });
});
