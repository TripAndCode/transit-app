import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import type { AgencyFeedQualityMonth } from "../../api/admin";
import { FeedQualityTrend } from "./FeedQualityTrend";

function month(m: string, over: Partial<AgencyFeedQualityMonth> = {}): AgencyFeedQualityMonth {
  return {
    month: `${m}-01`,
    through_date: `${m}-30`,
    covered_days: 30,
    window_days: 30,
    observed_trips: 693,
    planned_trips: 1000,
    rt_completeness_pct: 69.3,
    rt_completeness_missing: null,
    observed_stop_events: 8,
    planned_stop_events: 240,
    stop_plan_days: 30,
    stop_completeness_pct: 3.3,
    stop_completeness_missing: null,
    message_age_polls: 16,
    message_age_undated_polls: 0,
    message_age_stale_polls: 2,
    message_age_stale_pct: 12.5,
    message_age_p50_sec: 22,
    message_age_p95_sec: 38.3,
    message_age_missing: null,
    raw_samples: 1000,
    clamp_count: 3,
    clamp_pct: 0.3,
    clamp_missing: null,
    stop_time_updates: 1200,
    stop_id_pct: 100,
    arr_delay_pct: 7.2,
    schedule_relationship_trip_pct: 100,
    schedule_relationship_stop_pct: 100,
    field_coverage_missing: null,
    vehicle_positions_available: null,
    vehicle_positions_missing: "not_recorded",
    validator_ok_days: 28,
    validator_failed_days: 2,
    validator_files_validated: 1624,
    validator_rules: {
      W001: { severity: "WARNING", title: "Timestamps should be populated", occurrences: 40, files_with_rule: 40, days: 3 },
      E022: { severity: "ERROR", title: "Times are not increasing", occurrences: 3360, files_with_rule: 1624, days: 28 },
    },
    validator_missing: null,
    validator_ingested_at: "2026-10-31T00:40:00+00:00",
    poll_days: 30,
    in_service_min: 28800,
    poll_interval_sec: 30,
    polls_in_service: 56448,
    polls_expected: 57600,
    poll_success_pct: 98,
    feed_outage_min: 42.5,
    feed_outage_longest_min: 20,
    collector_outage_min: 9.5,
    collector_outage_longest_min: 9.5,
    polls_missing: null,
    outage_missing: null,
    peer_archive_rows: 120000,
    grade: "C",
    grade_failing: ["rt_completeness", "stale_polls"],
    grade_missing: [],
    grade_failing_fields: [],
    ...over,
  };
}

function renderTrend(months: AgencyFeedQualityMonth[]) {
  return render(
    <I18nextProvider i18n={i18n}>
      <FeedQualityTrend months={months} />
    </I18nextProvider>,
  );
}

/** The validator row's headline figure, without the rule list under it. */
function validatorFigure() {
  return screen.getByText("RT validator").nextElementSibling?.firstElementChild?.firstElementChild?.textContent;
}

describe("FeedQualityTrend", () => {
  it("says no month is built yet instead of drawing an empty table", async () => {
    await i18n.changeLanguage("en");
    renderTrend([]);
    expect(screen.getByText(/No monthly feed-quality figures yet/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows the newest month in full and one trend row per month, newest first", async () => {
    await i18n.changeLanguage("en");
    renderTrend([month("2026-10"), month("2026-09", { rt_completeness_pct: 71.0 })]);
    expect(
      screen.getByText("2026-10 · 30 days · trips and stop events counted through 2026-10-30"),
    ).toBeInTheDocument();
    expect(screen.getByText("693 of 1,000 planned trips")).toBeInTheDocument();
    expect(screen.getByText("median 22 s · 95th percentile 38.3 s")).toBeInTheDocument();
    // Live and archived polls both count, so the count names neither source.
    expect(screen.getByText("16 polls measured")).toBeInTheDocument();
    expect(screen.getByText(/arrival 7\.2%/)).toBeInTheDocument();
    const rows = within(screen.getByRole("table", { name: "Last six months" })).getAllByRole(
      "row",
    ) as HTMLTableRowElement[];
    expect(rows.map((r) => r.cells[0].textContent)).toEqual(["Month", "2026-10", "2026-09"]);
    expect(rows[2].cells[1].textContent).toBe("71.0%");
  });

  it("prints a missing figure as a dash with its reason, never as zero", async () => {
    await i18n.changeLanguage("en");
    renderTrend([
      month("2026-10", {
        message_age_p50_sec: null,
        message_age_p95_sec: null,
        message_age_polls: 0,
        message_age_undated_polls: 412,
        message_age_missing: "archive_stamped",
        stop_completeness_pct: null,
        stop_completeness_missing: "no_stop_plan",
        rt_completeness_pct: null,
        rt_completeness_missing: "no_plan_observed",
      }),
    ]);
    expect(screen.getByText("archived polls' file names give no capture time")).toBeInTheDocument();
    expect(screen.getByText("no timetable plans the days observed")).toBeInTheDocument();
    expect(screen.getByText("not recorded")).toBeInTheDocument();
    const row = within(screen.getByRole("table")).getAllByRole("row")[1] as HTMLTableRowElement;
    expect(row.cells[2].textContent).toBe("—");
    expect(row.cells[2].getAttribute("title")).toBe("stop plan not saved for these days");
    expect(row.cells[3].getAttribute("title")).toBe("archived polls' file names give no capture time");
  });

  it("shows the newest month's grade and the criteria it misses, in the trend too", async () => {
    await i18n.changeLanguage("en");
    renderTrend([month("2026-10"), month("2026-09", { grade: "A", grade_failing: [] })]);
    expect(
      screen.getByText("below threshold: trips reported 69.3%, 12.50% of polls older than 65 s"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Shown to admins only/)).toBeInTheDocument();
    expect(screen.getByText("12.50%")).toBeInTheDocument();
    expect(screen.getByText("2 of 16 polls measured")).toBeInTheDocument();
    const rows = within(screen.getByRole("table")).getAllByRole("row") as HTMLTableRowElement[];
    const last = rows[0].cells.length - 1;
    expect(rows[0].cells[last].textContent).toBe("Grade");
    expect(rows.slice(1).map((r) => r.cells[last].textContent)).toEqual(["C", "A"]);
    expect(rows[2].cells[last].getAttribute("title")).toBe("all four criteria met");
  });

  it("gives a month missing a needed figure no grade, and says which and why", async () => {
    await i18n.changeLanguage("en");
    renderTrend([
      month("2026-10", {
        rt_completeness_pct: null,
        rt_completeness_missing: "coverage_unknown",
        grade: null,
        grade_failing: ["stale_polls"],
        grade_missing: [{ criterion: "rt_completeness", reason: "coverage_unknown" }],
      }),
    ]);
    expect(screen.getByText("not graded: trips reported (trip IDs are not the timetable's)")).toBeInTheDocument();
    expect(screen.getByText("below threshold: 12.50% of polls older than 65 s")).toBeInTheDocument();
    const row = within(screen.getByRole("table")).getAllByRole("row")[1] as HTMLTableRowElement;
    const cell = row.cells[row.cells.length - 1];
    expect(cell.textContent).toBe("—");
    expect(cell.getAttribute("title")).toBe(
      "not graded: trips reported (trip IDs are not the timetable's) · below threshold: 12.50% of polls older than 65 s",
    );
  });

  it("names each failing criterion with the figure it was judged on", async () => {
    await i18n.changeLanguage("en");
    renderTrend([
      month("2026-10", {
        rt_completeness_pct: 99.1,
        message_age_stale_pct: 1.2,
        clamp_pct: 6.14,
        arr_delay_pct: 100,
        schedule_relationship_stop_pct: 98.9,
        grade: "C",
        grade_failing: ["clamp", "field_coverage"],
        grade_failing_fields: ["arr_delay", "schedule_relationship_stop"],
      }),
    ]);
    // The clamp share is judged to a tenth, so it is stated to a tenth.
    expect(
      screen.getByText(
        "below threshold: implausible readings 6.1%, field coverage (arrival 100.0%, stop status 98.9%)",
      ),
    ).toBeInTheDocument();
  });

  it("states a clamp share failing at its boundary as the tenth the gate judged", async () => {
    await i18n.changeLanguage("en");
    renderTrend([month("2026-10", { clamp_pct: 5.05, grade: "B", grade_failing: ["clamp"] })]);
    expect(screen.getByText("below threshold: implausible readings 5.1%")).toBeInTheDocument();
  });

  it("names the failing fields with their figures in Japanese too", async () => {
    await i18n.changeLanguage("ja");
    renderTrend([
      month("2026-10", {
        grade_failing: ["clamp", "field_coverage"],
        clamp_pct: 7,
        grade_failing_fields: ["stop_id", "arr_delay"],
        stop_id_pct: 0,
        arr_delay_pct: 0,
      }),
    ]);
    expect(
      screen.getByText("基準に届かない項目：異常値の割合 7.0%、項目の充足（stop_id 0.0%、到着 0.0%）"),
    ).toBeInTheDocument();
    await i18n.changeLanguage("en");
  });

  it("names field coverage alone when no field is singled out", async () => {
    await i18n.changeLanguage("en");
    renderTrend([month("2026-10", { stop_time_updates: 0, grade: "B", grade_failing: ["field_coverage"] })]);
    expect(screen.getByText("below threshold: field coverage")).toBeInTheDocument();
  });

  it("says a month built before the share of polls over 65 s was stored has it not recorded", async () => {
    await i18n.changeLanguage("en");
    renderTrend([
      month("2026-10", {
        message_age_stale_polls: null,
        message_age_stale_pct: null,
        grade: null,
        grade_failing: ["rt_completeness"],
        grade_missing: [{ criterion: "stale_polls", reason: "not_recorded" }],
      }),
    ]);
    const label = screen.getByText("Polls over 65 s");
    expect(label.nextElementSibling?.textContent).toBe("— not recorded");
  });

  it("shows the validator's days and each rule it counted, recorded beside the grade, not in it", async () => {
    await i18n.changeLanguage("en");
    renderTrend([month("2026-10")]);
    expect(validatorFigure()).toBe(
      "28 of 30 days validated 1,624 polls, one sampled hour a day",
    );
    expect(screen.getByText("could not run on 2 days")).toBeInTheDocument();
    const rules = screen.getAllByRole("listitem");
    expect(rules.map((li) => li.textContent)).toEqual([
      "E022 Times are not increasing, 3,360 in 1,624 polls on 28 days",
      "W001 Timestamps should be populated, 40 in 40 polls on 3 days",
    ]);
    // The vendor's wording stays English in either locale, marked as such.
    const title = within(rules[0]).getByText("Times are not increasing");
    expect(title.getAttribute("lang")).toBe("en");
    expect(screen.getByText(/recorded, not graded/)).toBeInTheDocument();
    expect(screen.getByText(/Rule names are the validator's own English wording/)).toBeInTheDocument();
  });

  it("reads a month with no validated day as not validated, never as no findings", async () => {
    await i18n.changeLanguage("en");
    renderTrend([
      month("2026-10", {
        validator_ok_days: 0,
        validator_failed_days: 0,
        validator_files_validated: null,
        validator_rules: null,
        validator_missing: "not_validated",
        validator_ingested_at: null,
      }),
    ]);
    expect(validatorFigure()).toBe("— not validated");
    expect(screen.queryByText("no rule fired")).toBeNull();
    expect(screen.queryByRole("listitem")).toBeNull();
  });

  it("says a validated month where no rule fired so, and a month built before the validator has it not recorded", async () => {
    await i18n.changeLanguage("en");
    const { unmount } = renderTrend([month("2026-10", { validator_rules: {}, validator_failed_days: 0 })]);
    expect(screen.getByText("no rule fired")).toBeInTheDocument();
    expect(screen.queryByText(/could not run/)).toBeNull();
    unmount();
    renderTrend([
      month("2026-10", {
        validator_ok_days: null,
        validator_failed_days: null,
        validator_files_validated: null,
        validator_rules: null,
        validator_missing: null,
        validator_ingested_at: null,
      }),
    ]);
    expect(validatorFigure()).toBe("— not recorded");
  });

  it("shows poll success and outage minutes beside the graded figures, ours apart from the feed's", async () => {
    await i18n.changeLanguage("en");
    renderTrend([month("2026-10"), month("2026-09", { poll_success_pct: 100, feed_outage_min: 0 })]);
    expect(screen.getByText("Poll success").nextElementSibling?.textContent).toBe(
      "98.0% 56,448 of 57,600 polls expected in service · every 30 s · 30 days · not graded",
    );
    expect(screen.getByText("Feed outages").nextElementSibling?.textContent).toBe("42.5 min longest 20 min");
    expect(screen.getByText("Our collector's outages").nextElementSibling?.textContent).toBe(
      "9.5 min longest 9.5 min · no agency was polled then, so this is our downtime, not the operator's",
    );
    const rows = within(screen.getByRole("table")).getAllByRole("row") as HTMLTableRowElement[];
    expect([5, 6, 7].map((i) => rows[0].cells[i].textContent)).toEqual(["Polls", "Feed outage", "Ours"]);
    expect([5, 6, 7].map((i) => rows[1].cells[i].textContent)).toEqual(["98.0%", "42.5 min", "9.5 min"]);
    expect([5, 6].map((i) => rows[2].cells[i].textContent)).toEqual(["100.0%", "0 min"]);
  });

  it("reads an outage whose cause is unknown as a dash with its reason, and an older month as not recorded", async () => {
    await i18n.changeLanguage("en");
    const unsplit = {
      feed_outage_min: null,
      feed_outage_longest_min: null,
      collector_outage_min: null,
      collector_outage_longest_min: null,
      outage_missing: "no_peer" as const,
    };
    const before = {
      ...unsplit,
      poll_days: null,
      in_service_min: null,
      poll_interval_sec: null,
      polls_in_service: null,
      polls_expected: null,
      poll_success_pct: null,
      polls_missing: null,
      outage_missing: null,
      peer_archive_rows: null,
    };
    renderTrend([month("2026-10", unsplit), month("2026-09", before)]);
    const why = "no other operator was polled, so whose outage it was is unknown";
    expect(screen.getByText("Feed outages").nextElementSibling?.textContent).toBe(`— ${why}`);
    expect(screen.getByText("Poll success").nextElementSibling?.textContent).toMatch(/^98\.0%/);
    const rows = within(screen.getByRole("table")).getAllByRole("row") as HTMLTableRowElement[];
    expect(rows[1].cells[6].getAttribute("title")).toBe(why);
    expect([5, 6, 7].map((i) => rows[2].cells[i].getAttribute("title"))).toEqual([
      "not recorded",
      "not recorded",
      "not recorded",
    ]);
  });

  it("speaks Japanese", async () => {
    await i18n.changeLanguage("ja");
    renderTrend([month("2026-10", { through_date: "2026-10-20" })]);
    expect(screen.getByText("2026-10 · 30日分 · 便と停留所の報告率は2026-10-20まで")).toBeInTheDocument();
    expect(screen.getByText("計画 1,000 便のうち 693 便")).toBeInTheDocument();
    expect(screen.getByText("計測した取得 16 回")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "直近6か月" })).toBeInTheDocument();
    expect(screen.getByText("基準に届かない項目：便の充足率 69.3%、65秒を超えた取得 12.50%")).toBeInTheDocument();
    expect(screen.getByText("30 日中 28 日を検証")).toBeInTheDocument();
    expect(screen.getByText("当方の収集停止")).toBeInTheDocument();
    expect(screen.getByText("フィードの停止").nextElementSibling?.textContent).toBe("42.5 分 最長 20 分");
    expect(screen.getAllByRole("listitem")[0].textContent).toBe(
      "E022 Times are not increasing、28 日の取得 1,624 回で 3,360 件",
    );
    await i18n.changeLanguage("en");
  });
});
