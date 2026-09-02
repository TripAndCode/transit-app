import { describe, it, expect } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import { HourlyHeatmap, type HourlyCell } from "./HourlyHeatmap";
import i18n from "../../i18n";

function renderHeatmap(cells: HourlyCell[]) {
  return renderWithProviders(
    <MemoryRouter>
      <HourlyHeatmap cells={cells} />
    </MemoryRouter>,
  );
}

// A single non-empty cell is enough to skip the empty-state early return and
// reach the legend toggle.
const ONE_CELL: HourlyCell[] = [{ date: "2026-06-01", hour: 8, avg_min: 2.0, samples: 50 }];

describe("HourlyHeatmap legend", () => {
  it("labels the four swatches with delayBand()'s actual thresholds (<1.5, 1.5-3, 3-5, >5), not stale 2/5/10 cutoffs", async () => {
    await i18n.changeLanguage("en");
    renderHeatmap(ONE_CELL);
    fireEvent.click(screen.getByRole("button", { name: i18n.t("reports.heatmap.legend_aria") }));

    expect(screen.getByText("< 1.5 min")).toBeInTheDocument();
    expect(screen.getByText("1.5–3 min")).toBeInTheDocument();
    expect(screen.getByText("3–5 min")).toBeInTheDocument();
    expect(screen.getByText("> 5 min")).toBeInTheDocument();

    // None of the old, mismatched labels should survive.
    expect(screen.queryByText("<2 min")).toBeNull();
    expect(screen.queryByText("2-5")).toBeNull();
    expect(screen.queryByText("5-10")).toBeNull();
    expect(screen.queryByText(">10")).toBeNull();
  });
});
