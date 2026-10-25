import { describe, it, expect, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { ModelPage } from "./ModelPage";
import * as hooks from "../api/hooks";
import type { MlEvaluations } from "../api/types";

function evaluations(partial: Partial<MlEvaluations> = {}): MlEvaluations {
  return {
    latest: {
      run_date: "2026-10-25",
      adopted: true,
      failures: [],
      skill: 0.051,
      coverage: 0.809,
      interval_coverage: 0.809,
      thresholds: { min_skill: 0.05, coverage_low: 0.75, coverage_high: 0.85, min_model_days: 56 },
      point_metric: "mean",
      serving_model_version: "2026-10-24",
      origin_skill: { count: 28, min: -0.02, median: 0.044, max: 0.134 },
      agencies: [
        { agency_id: 8, agency_name: "Hiroden", model_status: "model", skill: 0.066, lgbm_mae: 1.0, b0_mae: 1.07, days_of_data: 140, forecast_live: true },
        { agency_id: 54, agency_name: "Asahi", model_status: "b0", skill: null, lgbm_mae: 1.2, b0_mae: 1.2, days_of_data: 20, forecast_live: false },
      ],
    },
    history: [
      { run_date: "2026-10-25", adopted: true, overall_skill: 0.051, point_metric: "q50" },
      { run_date: "2026-10-18", adopted: true, overall_skill: 0.063, point_metric: "mean" },
    ],
    forecast_in_use: true,
    ...partial,
  };
}

function renderPage(data: MlEvaluations | undefined) {
  vi.spyOn(hooks, "useMlEvaluations").mockReturnValue({ data, isPending: false, error: null, refetch: vi.fn() } as never);
  renderWithProviders(
    <MemoryRouter>
      <ModelPage />
    </MemoryRouter>,
  );
}

describe("ModelPage", () => {
  it("shows an adopted week's verdict, skill and the model in use", () => {
    renderPage(evaluations());
    expect(screen.getByTestId("model-verdict")).toHaveTextContent("Adopted");
    expect(screen.getByTestId("model-skill")).toHaveTextContent("5.1% less error");
    expect(screen.getByText("Model in use: 2026-10-24")).toBeInTheDocument();
  });

  it("words a not-adopted week's reasons in the reader's language, and survives an unknown code", () => {
    const data = evaluations();
    data.latest = {
      ...data.latest!,
      adopted: false,
      failures: [
        { code: "skill_below_min", value: 0.046, agencies: [] },
        { code: "something_new", value: null, agencies: [] },
      ],
    };
    renderPage(data);
    expect(screen.getByTestId("model-verdict")).toHaveTextContent("Not adopted");
    const reasons = screen.getByTestId("model-failures");
    expect(reasons).toHaveTextContent("Error improved by only 4.6% against B0, short of the 5.0% bar");
    expect(reasons).toHaveTextContent("Another check did not pass (something_new)");
  });

  it("shows no skill for an agency on B0, rather than 0%", () => {
    renderPage(evaluations());
    const row = within(screen.getByTestId("model-agencies")).getByText("Asahi").closest("tr")!;
    expect(row).toHaveTextContent("On B0 (too little history)");
    expect(row).toHaveTextContent("—");
    expect(row).not.toHaveTextContent("0.0%");
  });

  it("flags the week the scoring metric changed", () => {
    renderPage(evaluations());
    const history = screen.getByTestId("model-history");
    expect(history).toHaveTextContent("From this week on, scored with the median");
  });

  it("says when the ML forecast is not on the heatmap", () => {
    renderPage(evaluations({ forecast_in_use: false }));
    expect(screen.getByText(/the ML forecast flag is off/)).toBeInTheDocument();
  });

  it("shows the coverage the adoption gate judged, and the published slot range apart from it", () => {
    const data = evaluations();
    data.latest = { ...data.latest!, coverage: 0.74, interval_coverage: 0.781 };
    renderPage(data);
    const card = screen.getByTestId("model-coverage");
    expect(card).toHaveTextContent("74.0%");
    expect(card).toHaveTextContent("Per time slot, as published: 78.1%");
  });

  it("shows a calm empty state before the first evaluation", () => {
    renderPage(evaluations({ latest: null, history: [] }));
    expect(screen.getByText("No evaluation yet. It is updated every Sunday.")).toBeInTheDocument();
  });
});
