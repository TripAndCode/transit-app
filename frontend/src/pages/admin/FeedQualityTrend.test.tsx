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
    expect(screen.getByText(/2026-10 · counted through 2026-10-30 \(30 days\)/)).toBeInTheDocument();
    expect(screen.getByText("693 of 1,000 planned trips")).toBeInTheDocument();
    expect(screen.getByText("median 22 s · 95th percentile 38.3 s")).toBeInTheDocument();
    expect(screen.getByText(/arrival 7\.2%/)).toBeInTheDocument();
    const rows = within(screen.getByRole("table", { name: "Last six months" })).getAllByRole("row");
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
        message_age_missing: "archive_stamped",
        stop_completeness_pct: null,
        stop_completeness_missing: "no_stop_plan",
      }),
    ]);
    expect(screen.getByText("archived polls take their time from the feed itself")).toBeInTheDocument();
    expect(screen.getByText("not recorded")).toBeInTheDocument();
    const row = within(screen.getByRole("table")).getAllByRole("row")[1];
    expect(row.cells[2].textContent).toBe("—");
    expect(row.cells[2].getAttribute("title")).toBe("stop plan not saved for these days");
    expect(row.cells[3].getAttribute("title")).toBe("archived polls take their time from the feed itself");
  });

  it("speaks Japanese", async () => {
    await i18n.changeLanguage("ja");
    renderTrend([month("2026-10")]);
    expect(screen.getByText("計画 1,000 便のうち 693 便")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "直近6か月" })).toBeInTheDocument();
    await i18n.changeLanguage("en");
  });
});
