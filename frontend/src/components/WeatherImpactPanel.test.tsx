import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { WeatherImpactPanel } from "./WeatherImpactPanel";
import * as hooks from "../api/hooks";
import type { WeatherImpactResponse } from "../api/types";

const CTX = { from: "2026-04-01", to: "2026-04-08", dow: "all", time_band: "all", service: "all", routes: [] } as const;

const BASE: WeatherImpactResponse = {
  available: true,
  wet_threshold_mm: 5,
  min_days_per_bucket: 3,
  wet: { days: 4, samples: 400, on_time_pct: 70.0, avg_delay_min: 2.0 },
  dry: { days: 3, samples: 300, on_time_pct: 90.0, avg_delay_min: 1.0 },
  on_time_gap_pt: 20.0,
  avg_delay_gap_min: 1.0,
  observed_days: 7,
  unobserved_days: 1,
  coverage_pct: 87.5,
  latest_observed_date: "2026-04-07",
  station_id: "44132",
  station_name: "Tokyo",
  ctx: CTX as never,
  attribution: "Source: Japan Meteorological Agency (past weather observations).",
};

function mockWeatherImpact(
  result: Partial<{ data: WeatherImpactResponse; isLoading: boolean; error: unknown }>,
) {
  vi.spyOn(hooks, "useWeatherImpact").mockReturnValue({
    data: undefined,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
    ...result,
  } as never);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("WeatherImpactPanel", () => {
  it("renders both sides, the signed gaps, and the observed-not-forecast framing", () => {
    mockWeatherImpact({ data: BASE });
    renderWithProviders(<WeatherImpactPanel aid={1} ctx={CTX as never} />);

    expect(screen.getByText("Observed days — not a forecast")).toBeInTheDocument();
    expect(screen.getByText("Wet days")).toBeInTheDocument();
    expect(screen.getByText("Dry days")).toBeInTheDocument();
    expect(screen.getByText("70.0%")).toBeInTheDocument();
    expect(screen.getByText("90.0%")).toBeInTheDocument();
    expect(screen.getByText("2.00 min")).toBeInTheDocument();
    expect(screen.getByText("1.00 min")).toBeInTheDocument();
    // Both gaps read positive in the same "wet days were worse" direction.
    expect(screen.getByText("+20.0 pt")).toBeInTheDocument();
    expect(screen.getByText("+1.00 min")).toBeInTheDocument();
    // The descriptive-not-causal caveat and JMA attribution both stay attached.
    expect(screen.getByText(/not a rain-attributed effect/i)).toBeInTheDocument();
    expect(screen.getByText(/Japan Meteorological Agency/)).toBeInTheDocument();
  });

  it("reports coverage, the representative station and the latest observation", () => {
    mockWeatherImpact({ data: BASE });
    renderWithProviders(<WeatherImpactPanel aid={1} ctx={CTX as never} />);

    expect(screen.getByText(/Observed on 7 of 8 service days/)).toBeInTheDocument();
    expect(screen.getByText(/Representative station: Tokyo/)).toBeInTheDocument();
    expect(screen.getByText(/Latest observation: 2026-04-07/)).toBeInTheDocument();
  });

  it("explains a too-thin side instead of showing a comparison", () => {
    mockWeatherImpact({
      data: {
        ...BASE,
        available: false,
        dry: { days: 1, samples: 100, on_time_pct: null, avg_delay_min: null },
        on_time_gap_pt: null,
        avg_delay_gap_min: null,
      },
    });
    renderWithProviders(<WeatherImpactPanel aid={1} ctx={CTX as never} />);

    expect(screen.getByText(/each side needs at least 3/i)).toBeInTheDocument();
    // The thin side's counts stay visible; only its figures are withheld.
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("70.0%")).toBeInTheDocument();
  });

  it("renders nothing when this agency has no observed precipitation in range", () => {
    mockWeatherImpact({
      data: {
        ...BASE,
        available: false,
        wet: { days: 0, samples: 0, on_time_pct: null, avg_delay_min: null },
        dry: { days: 0, samples: 0, on_time_pct: null, avg_delay_min: null },
        on_time_gap_pt: null,
        avg_delay_gap_min: null,
        observed_days: 0,
        unobserved_days: 8,
        coverage_pct: 0,
        latest_observed_date: null,
        station_id: null,
        station_name: null,
      },
    });
    const { container } = renderWithProviders(<WeatherImpactPanel aid={1} ctx={CTX as never} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a skeleton while loading", () => {
    mockWeatherImpact({ isLoading: true });
    const { container } = renderWithProviders(<WeatherImpactPanel aid={1} ctx={CTX as never} />);
    expect(container.querySelector(".skeleton")).toBeInTheDocument();
  });
});
