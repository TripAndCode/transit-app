import { afterEach, describe, it, expect } from "vitest";
import { cleanup, screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import i18n from "../i18n";
import { DefinitionMetaBlock } from "./DefinitionMetaBlock";
import type { DefinitionMeta, FeedQuality, SourceMix, TimetableVersion } from "../api/types";

const LEGACY: DefinitionMeta = {
  preset: "legacy_60s",
  early_tolerance_sec: null,
  late_tolerance_sec: 60,
  early_departure_sec: null,
  exclusion_threshold_sec: 7200,
  measurement_point: "all_stops_all_observations",
  dedup_rule: "latest_observation_per_stop_event",
  method_version: "2026.11.0",
  source: "aggregates",
  built_at: null,
  value_basis: "feed_prediction",
  timetable_versions: null,
  source_mix: null,
};

const ONE_AGENCY: TimetableVersion[] = [
  { agency_id: 1, name: "gtfs_static_20260401", fetched_on: "2026-04-01", days: 28 },
  { agency_id: 1, name: null, fetched_on: null, days: 3 },
];

// 00:30 JST on 2026-11-06, which is still 2026-11-05 in UTC.
const BUILT_AT = "2026-11-05T15:30:00Z";

describe("DefinitionMetaBlock", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("keeps the method behind one 'How this is calculated' disclosure", () => {
    renderWithProviders(<DefinitionMetaBlock definition={LEGACY} />);
    const summary = screen.getByText("How this is calculated");
    expect(summary.tagName).toBe("SUMMARY");
    expect(summary.closest("details")).not.toHaveAttribute("open");
  });

  it("says how the figures are made in plain words, without preset ids or raw seconds", () => {
    renderWithProviders(<DefinitionMetaBlock definition={LEGACY} />);
    const block = screen.getByTestId("definition-meta");
    expect(block).toHaveTextContent("Average of every recorded departure at every stop.");
    expect(block).toHaveTextContent("When a departure is reported more than once, the latest report counts.");
    expect(block).toHaveTextContent("Readings more than 2 hours off are ignored as errors.");
    expect(block).toHaveTextContent("On time (legacy) = no more than 1 min late (early departures count as on time).");
    expect(block).not.toHaveTextContent("legacy_60s");
    expect(block).not.toHaveTextContent("7200");
  });

  it("calls the default, the industry window, by its plain name in both languages", async () => {
    const standard: DefinitionMeta = { ...LEGACY, preset: "standard_m1_p5", early_tolerance_sec: 60, late_tolerance_sec: 300 };
    renderWithProviders(<DefinitionMetaBlock definition={standard} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent(
      "On time (industry standard) = no more than 5 min late and no more than 1 min early.",
    );
    expect(screen.getByTestId("definition-meta")).not.toHaveTextContent("standard_m1_p5");
    cleanup();
    await i18n.changeLanguage("ja");
    renderWithProviders(<DefinitionMetaBlock definition={standard} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent("定時（業界標準） = 遅れ5分以内、早発1分以内。");
  });

  it("runs Japanese sentences together, without a half-width space between them", async () => {
    await i18n.changeLanguage("ja");
    renderWithProviders(<DefinitionMetaBlock definition={LEGACY} />);
    expect(screen.getByTestId("definition-meta").querySelector("p")?.textContent).toContain("平均です。同じ発車");
  });

  it("states a custom tolerance, early and late", () => {
    renderWithProviders(
      <DefinitionMetaBlock definition={{ ...LEGACY, preset: "custom", early_tolerance_sec: 30, late_tolerance_sec: 120 }} />,
    );
    expect(screen.getByTestId("definition-meta")).toHaveTextContent(
      "On time = no more than 2 min late and no more than 30 s early.",
    );
  });

  it("defines the early-departure rate where the figures carry one", () => {
    renderWithProviders(<DefinitionMetaBlock definition={{ ...LEGACY, early_departure_sec: 60 }} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent(
      "Early-departure rate = the share of departures more than 1 min early.",
    );
    cleanup();
    renderWithProviders(<DefinitionMetaBlock definition={LEGACY} />);
    expect(screen.getByTestId("definition-meta")).not.toHaveTextContent("Early-departure");
  });

  it("says nothing about on time for a report with no tolerance", () => {
    renderWithProviders(
      <DefinitionMetaBlock definition={{ ...LEGACY, preset: null, late_tolerance_sec: null }} />,
    );
    expect(screen.getByTestId("definition-meta")).not.toHaveTextContent("On time");
  });

  it("shows an identifier this build has no words for as the API sent it", () => {
    renderWithProviders(
      <DefinitionMetaBlock
        definition={{ ...LEGACY, measurement_point: "some_other_measurement_point", dedup_rule: "some_other_dedup_rule" }}
      />,
    );
    const block = screen.getByTestId("definition-meta");
    expect(block).toHaveTextContent("some_other_measurement_point");
    expect(block).toHaveTextContent("some_other_dedup_rule");
  });

  it("names the method version and the JST day the aggregates were built", () => {
    renderWithProviders(<DefinitionMetaBlock definition={{ ...LEGACY, built_at: BUILT_AT }} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent(
      "Method version 2026.11.0, aggregates built: Nov 6, 2026.",
    );
  });

  it("writes the stamp the way a Japanese document does", async () => {
    await i18n.changeLanguage("ja");
    renderWithProviders(<DefinitionMetaBlock definition={{ ...LEGACY, built_at: BUILT_AT }} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent("算出方法 2026.11.0、集計日 2026年11月6日。");
  });

  it("says the build day is unknown when no build is recorded", () => {
    renderWithProviders(<DefinitionMetaBlock definition={LEGACY} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent("Method version 2026.11.0, aggregates built: unknown.");
  });

  it("says a live scan has no build day, even if handed one", () => {
    renderWithProviders(<DefinitionMetaBlock definition={{ ...LEGACY, source: "live", built_at: BUILT_AT }} />);
    const block = screen.getByTestId("definition-meta");
    expect(block).toHaveTextContent("Method version 2026.11.0, aggregates built: not applicable (live scan).");
    expect(block).not.toHaveTextContent("Nov 6");
  });

  it("says the figures are made from the feed's predictions, not observed passages", () => {
    renderWithProviders(<DefinitionMetaBlock definition={LEGACY} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent(
      "Figures are computed from the delays the operator's feed last predicted for each stop, not from observed passing times.",
    );
  });

  it("says what the figures are made from the way a Japanese document does", async () => {
    await i18n.changeLanguage("ja");
    renderWithProviders(<DefinitionMetaBlock definition={LEGACY} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent(
      "数値は、事業者のフィードが停留所ごとに最後に配信した遅延の予測値から算出したもので、通過の観測値ではありません。",
    );
  });

  it("shows a value basis this build has no words for as the API sent it", () => {
    renderWithProviders(<DefinitionMetaBlock definition={{ ...LEGACY, value_basis: "some_other_basis" }} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent("some_other_basis");
  });

  it("names the timetables in force and the days each covers", () => {
    renderWithProviders(<DefinitionMetaBlock definition={{ ...LEGACY, timetable_versions: ONE_AGENCY }} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent(
      "Timetable: the version fetched Apr 1, 2026: 28 days; none recorded: 3 days (of 31 days).",
    );
  });

  it("names the timetables the way a Japanese document does", async () => {
    await i18n.changeLanguage("ja");
    renderWithProviders(<DefinitionMetaBlock definition={{ ...LEGACY, timetable_versions: ONE_AGENCY }} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent(
      "時刻表：2026年4月1日取得版 28日間、記録なし 3日間（全31日間）。",
    );
  });

  it("names a timetable whose name carries no day by its name", () => {
    const undated: TimetableVersion[] = [{ agency_id: 1, name: "hiroden_static", fetched_on: null, days: 1 }];
    renderWithProviders(<DefinitionMetaBlock definition={{ ...LEGACY, timetable_versions: undated }} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent("Timetable: hiroden_static: 1 day (of 1 day).");
  });

  const HASHED: TimetableVersion[] = [
    { agency_id: 1, name: "gtfs_static_20260401", fetched_on: "2026-04-01", days: 20, content_hash: "a1b2c3d4" },
    { agency_id: 1, name: "hiroden_static", fetched_on: null, days: 5, content_hash: "e5f60718" },
    { agency_id: 1, name: "gtfs_static_20260501", fetched_on: "2026-05-01", days: 3, content_hash: null },
    { agency_id: 1, name: null, fetched_on: null, days: 3 },
  ];

  it("names each timetable the archive maps by its content too, and an unmapped one without", () => {
    renderWithProviders(<DefinitionMetaBlock definition={{ ...LEGACY, timetable_versions: HASHED }} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent(
      "Timetable: the version fetched Apr 1, 2026 (content hash a1b2c3d4): 20 days; " +
        "hiroden_static (content hash e5f60718): 5 days; the version fetched May 1, 2026: 3 days; " +
        "none recorded: 3 days (of 31 days).",
    );
  });

  it("names each mapped timetable by its content the way a Japanese document does", async () => {
    await i18n.changeLanguage("ja");
    renderWithProviders(<DefinitionMetaBlock definition={{ ...LEGACY, timetable_versions: HASHED }} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent(
      "時刻表：2026年4月1日取得版（内容ハッシュ a1b2c3d4）20日間、hiroden_static（内容ハッシュ e5f60718）5日間、" +
        "2026年5月1日取得版 3日間、記録なし 3日間（全31日間）。",
    );
  });

  it("says nothing of timetables the API did not name", () => {
    renderWithProviders(<DefinitionMetaBlock definition={LEGACY} />);
    expect(screen.getByTestId("definition-meta")).not.toHaveTextContent("Timetable");
  });

  it("lists each agency's timetables under its name when the figures span several", () => {
    const board: TimetableVersion[] = [
      { agency_id: 1, name: "gtfs_static_20260301", fetched_on: "2026-03-01", days: 7 },
      { agency_id: 2, name: "hiroden_static", fetched_on: null, days: 1 },
      { agency_id: 2, name: null, fetched_on: null, days: 6 },
    ];
    renderWithProviders(
      <DefinitionMetaBlock
        definition={{ ...LEGACY, timetable_versions: board }}
        agencyNames={new Map([[1, "Hiroden"], [2, "HiroBus"]])}
      />,
    );
    const items = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toEqual([
      "Hiroden — the version fetched Mar 1, 2026: 7 days (of 7 days)",
      "HiroBus — hiroden_static: 1 day; none recorded: 6 days (of 7 days)",
    ]);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent("Timetables");
  });

  const SOURCES: SourceMix[] = [{ agency_id: 1, archive_days: 28, live_days: 3, none_days: 0 }];
  const SOURCES_EXPLAINED_EN =
    "Archive days hold every poll the collector made that day; live days hold only the part the app received in real time.";

  it("names the days each source supplied the observations on, and says what each source is", () => {
    renderWithProviders(<DefinitionMetaBlock definition={{ ...LEGACY, source_mix: SOURCES }} />);
    const block = screen.getByTestId("definition-meta");
    expect(block).toHaveTextContent("Observations: archive 28 days, live 3 days (of 31 days).");
    expect(block).toHaveTextContent(SOURCES_EXPLAINED_EN);
  });

  it("names the sources the way a Japanese document does", async () => {
    await i18n.changeLanguage("ja");
    renderWithProviders(<DefinitionMetaBlock definition={{ ...LEGACY, source_mix: SOURCES }} />);
    const block = screen.getByTestId("definition-meta");
    expect(block).toHaveTextContent("観測データ：アーカイブ 28日間、ライブ 3日間（全31日間）。");
    expect(block).toHaveTextContent(
      "アーカイブの日は収集サーバーがその日に取得した全データ、ライブの日はアプリがリアルタイムで受け取った一部のデータのみです。",
    );
  });

  it("names only the sources that supplied a day, and explains none when no day was recorded", () => {
    renderWithProviders(
      <DefinitionMetaBlock
        definition={{ ...LEGACY, source_mix: [{ agency_id: 1, archive_days: 0, live_days: 0, none_days: 1 }] }}
      />,
    );
    const block = screen.getByTestId("definition-meta");
    expect(block).toHaveTextContent("Observations: none recorded 1 day (of 1 day).");
    expect(block).not.toHaveTextContent("Archive days");
  });

  it("says nothing of sources the API did not name", () => {
    renderWithProviders(<DefinitionMetaBlock definition={LEGACY} />);
    expect(screen.getByTestId("definition-meta")).not.toHaveTextContent("Observations");
  });

  it("lists each agency's sources under its name when the figures span several", () => {
    const board: SourceMix[] = [
      { agency_id: 1, archive_days: 7, live_days: 0, none_days: 0 },
      { agency_id: 2, archive_days: 0, live_days: 1, none_days: 6 },
    ];
    renderWithProviders(
      <DefinitionMetaBlock
        definition={{ ...LEGACY, source_mix: board }}
        agencyNames={new Map([[1, "Hiroden"], [2, "HiroBus"]])}
      />,
    );
    const items = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toEqual([
      "Hiroden — archive 7 days (of 7 days)",
      "HiroBus — live 1 day, none recorded 6 days (of 7 days)",
    ]);
    const block = screen.getByTestId("definition-meta");
    expect(block).toHaveTextContent("Observations");
    expect(block).toHaveTextContent(SOURCES_EXPLAINED_EN);
  });

  const QUALITY: FeedQuality = {
    agency_id: 1,
    coverage_pct: 93.2,
    observed_trips: 11500,
    coverage_planned_trips: 12345,
    coverage_missing: null,
    coverage_low: false,
    clamp_pct: 0.42,
    clamp_missing: null,
    clamp_high: false,
    min_coverage_pct: 70,
    max_clamp_pct: 5,
  };

  it("states the period's completeness and clamp share for the whole agency", () => {
    renderWithProviders(<DefinitionMetaBlock definition={{ ...LEGACY, feed_quality: [QUALITY] }} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent(
      "Feed quality for the whole agency: Observation coverage 93.2% (11,500 of 12,345 planned trips). Implausible readings 0.4%.",
    );
  });

  it("states the figures the way a Japanese document does", async () => {
    await i18n.changeLanguage("ja");
    renderWithProviders(<DefinitionMetaBlock definition={{ ...LEGACY, feed_quality: [QUALITY] }} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent(
      "フィード品質（事業者全体）：観測カバー率 93.2%（計画12,345便中11,500便）、異常値 0.4%。",
    );
  });

  it("states a figure the gate flags in the board's own words, with no alarm styling", async () => {
    const flagged: FeedQuality = { ...QUALITY, coverage_pct: 65, coverage_low: true, clamp_pct: 6.25, clamp_high: true };
    renderWithProviders(<DefinitionMetaBlock definition={{ ...LEGACY, feed_quality: [flagged] }} />);
    const block = screen.getByTestId("definition-meta");
    expect(block).toHaveTextContent(
      "Feed quality for the whole agency: Observation coverage 65.0% (below 70%). Implausible readings 6.3% (above 5%).",
    );
    expect(block.querySelector("[class*='alert'], [class*='warn'], [class*='danger'], [style*='color']")).toBeNull();
    await i18n.changeLanguage("ja");
    expect(block).toHaveTextContent("フィード品質（事業者全体）：観測カバー率 65.0%（70%未満）、異常値 6.3%（5%超）。");
  });

  it.each([
    ["no_data", "no analyzed observations in the period", "期間内の集計済みの観測なし"],
    ["unmatched", "this feed's trips can't be matched to the timetable", "この配信の便は時刻表の便と照合できません"],
    ["no_plan", "no timetable plans this period", "期間に時刻表の計画なし"],
    ["no_plan_observed", "no timetable plans the days observed", "観測した日に時刻表の計画なし"],
    ["unreadable", "the plan could not be read", "計画を読み込めませんでした"],
  ] as const)("says why coverage reads no figure (%s), never 0", async (reason, en, ja) => {
    const missing: FeedQuality = {
      ...QUALITY,
      coverage_pct: null,
      observed_trips: null,
      coverage_planned_trips: null,
      coverage_missing: reason,
      clamp_pct: null,
      clamp_missing: "no_data",
    };
    renderWithProviders(<DefinitionMetaBlock definition={{ ...LEGACY, feed_quality: [missing] }} />);
    const block = screen.getByTestId("definition-meta");
    expect(block).toHaveTextContent(
      `Feed quality for the whole agency: Observation coverage — (${en}). Implausible readings — (no analyzed observations in the period).`,
    );
    expect(block).not.toHaveTextContent("0.0%");
    await i18n.changeLanguage("ja");
    expect(block).toHaveTextContent(`フィード品質（事業者全体）：観測カバー率 —（${ja}）、異常値 —（期間内の集計済みの観測なし）。`);
  });

  it("lists each agency's figures under its name when the figures span several", () => {
    renderWithProviders(
      <DefinitionMetaBlock
        definition={{ ...LEGACY, feed_quality: [QUALITY, { ...QUALITY, agency_id: 2, clamp_pct: 0 }] }}
        agencyNames={new Map([[1, "Hiroden"], [2, "HiroBus"]])}
      />,
    );
    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Hiroden — Observation coverage 93.2% (11,500 of 12,345 planned trips). Implausible readings 0.4%",
      "HiroBus — Observation coverage 93.2% (11,500 of 12,345 planned trips). Implausible readings 0.0%",
    ]);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent("Feed quality for the whole agency");
  });

  it("renders a definition saved before the stamp carried the figures", () => {
    const saved: DefinitionMeta = { ...LEGACY };
    delete saved.feed_quality;
    renderWithProviders(<DefinitionMetaBlock definition={saved} />);
    expect(screen.getByTestId("definition-meta")).not.toHaveTextContent("Feed quality");
    expect(screen.getByTestId("definition-meta")).toHaveTextContent("Average of every recorded departure at every stop.");
  });

  it("writes the live and unknown stamps the way a Japanese document does", async () => {
    await i18n.changeLanguage("ja");
    const { unmount } = renderWithProviders(<DefinitionMetaBlock definition={{ ...LEGACY, source: "live" }} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent("算出方法 2026.11.0、集計日 対象外（ライブ集計）。");
    unmount();
    renderWithProviders(<DefinitionMetaBlock definition={LEGACY} />);
    expect(screen.getByTestId("definition-meta")).toHaveTextContent("算出方法 2026.11.0、集計日 不明。");
  });
});
