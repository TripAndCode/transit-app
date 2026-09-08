import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import i18n from "../../i18n";
import { renderWithProviders } from "../../test/renderWithProviders";
import { DailyChart } from "./DailyChart";
import type { TrendDay } from "../../api/types";

function day(overrides: Partial<TrendDay> & { date: string }): TrendDay {
  return {
    avg_min: 2.0,
    samples: 100,
    top_offenders: [],
    ...overrides,
  };
}

describe("DailyChart", () => {
  it("renders the empty state with no days", () => {
    renderWithProviders(<DailyChart days={[]} />);
    expect(screen.getByText(i18n.t("reports.daily.empty"))).toBeInTheDocument();
  });

  it("does not show the smoothed-average legend when no day has a smoothed value", () => {
    renderWithProviders(
      <DailyChart days={[day({ date: "2026-05-18", avg_min_smoothed: null }), day({ date: "2026-05-19" })]} />,
    );
    expect(screen.queryByText(i18n.t("reports.daily.smoothed_label"))).not.toBeInTheDocument();
  });

  it("shows the smoothed-average legend when at least one day has a smoothed value", () => {
    renderWithProviders(
      <DailyChart
        days={[
          day({ date: "2026-05-18", avg_min: 1.0, avg_min_smoothed: 1.0 }),
          day({ date: "2026-05-19", avg_min: 3.0, avg_min_smoothed: 2.0 }),
        ]}
      />,
    );
    expect(screen.getByText(i18n.t("reports.daily.smoothed_label"))).toBeInTheDocument();
  });

  it("marks a schedule-revision boundary date that appears in days", () => {
    renderWithProviders(
      <DailyChart
        days={[day({ date: "2026-05-18" }), day({ date: "2026-05-19" }), day({ date: "2026-05-20" })]}
        revisionBoundaries={["2026-05-19"]}
      />,
    );
    expect(screen.getByText(i18n.t("reports.daily.revision_boundary_label"))).toBeInTheDocument();
  });

  it("skips a boundary date absent from days without crashing", () => {
    renderWithProviders(
      <DailyChart
        days={[day({ date: "2026-05-18" }), day({ date: "2026-05-19" })]}
        revisionBoundaries={["2099-01-01"]}
      />,
    );
    expect(screen.queryByText(i18n.t("reports.daily.revision_boundary_label"))).not.toBeInTheDocument();
  });
});
