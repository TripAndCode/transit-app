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
    grade: "C",
    grade_failing: ["rt_completeness", "stale_polls"],
    grade_missing: [],
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
    expect(screen.getByText("below threshold: trips reported, polls over 65 s")).toBeInTheDocument();
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
    expect(screen.getByText("below threshold: polls over 65 s")).toBeInTheDocument();
    const row = within(screen.getByRole("table")).getAllByRole("row")[1] as HTMLTableRowElement;
    const cell = row.cells[row.cells.length - 1];
    expect(cell.textContent).toBe("—");
    expect(cell.getAttribute("title")).toBe(
      "not graded: trips reported (trip IDs are not the timetable's) · below threshold: polls over 65 s",
    );
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
    const rules = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(rules).toEqual([
      "E022 Times are not increasing: 3,360 in 1,624 polls on 28 days",
      "W001 Timestamps should be populated: 40 in 40 polls on 3 days",
    ]);
    expect(screen.getByText(/recorded, not graded/)).toBeInTheDocument();
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

  it("speaks Japanese", async () => {
    await i18n.changeLanguage("ja");
    renderTrend([month("2026-10", { through_date: "2026-10-20" })]);
    expect(screen.getByText("2026-10 · 30日分 · 便と停留所の報告率は2026-10-20まで")).toBeInTheDocument();
    expect(screen.getByText("計画 1,000 便のうち 693 便")).toBeInTheDocument();
    expect(screen.getByText("計測した取得 16 回")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "直近6か月" })).toBeInTheDocument();
    expect(screen.getByText("基準に届かない項目：便の充足率、65秒を超えた取得")).toBeInTheDocument();
    expect(screen.getByText("30 日中 28 日を検証")).toBeInTheDocument();
    await i18n.changeLanguage("en");
  });
});
