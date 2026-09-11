import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { WeatherDelayPanel } from "./WeatherDelayPanel";
import * as hooks from "../api/hooks";
import type { WeatherDelayResponse } from "../api/types";

const CTX = { from: "2026-04-01", to: "2026-04-30", dow: "all", time_band: "all", service: "all", routes: [] } as const;

function mockWeatherDelay(result: Partial<{ data: WeatherDelayResponse; isLoading: boolean; error: unknown }>) {
  vi.spyOn(hooks, "useWeatherDelay").mockReturnValue({
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

describe("WeatherDelayPanel", () => {
  it("renders one calm line when no station is configured, not an error banner", () => {
    mockWeatherDelay({
      data: {
        available: false,
        station: null,
        wet_day_threshold_mm: 1.0,
        wet: { days: 0, samples: 0, avg_delay_sec: null, avg_precip_mm: null },
        dry: { days: 0, samples: 0, avg_delay_sec: null, avg_precip_mm: null },
        delta_sec: null,
        low_confidence: false,
        ctx: CTX,
        disclaimer: "Observed rainfall, not a forecast or a causal claim.",
        attribution: "Source: example weather service",
      },
    });
    renderWithProviders(<WeatherDelayPanel aid={1} ctx={CTX as never} />);

    expect(screen.getByText(/no observation station is configured/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("Observed rainfall, not a forecast or a causal claim.")).not.toBeInTheDocument();
  });

  it("shows a muted low-confidence badge when either group has too few days", () => {
    mockWeatherDelay({
      data: {
        available: true,
        station: { station_id: "47765", station_name: "Example Station", note: "Nearest documented station." },
        wet_day_threshold_mm: 1.0,
        wet: { days: 3, samples: 40, avg_delay_sec: 95.5, avg_precip_mm: 12.3 },
        dry: { days: 22, samples: 660, avg_delay_sec: 60.2, avg_precip_mm: 0.0 },
        delta_sec: 35.3,
        low_confidence: true,
        ctx: CTX,
        disclaimer: "Observed rainfall, not a forecast or a causal claim.",
        attribution: "Source: example weather service",
      },
    });
    renderWithProviders(<WeatherDelayPanel aid={1} ctx={CTX as never} />);

    expect(screen.getByText("Low confidence")).toBeInTheDocument();
  });

  it("renders the wet/dry comparison, sample counts, station, and server disclaimer/attribution verbatim", () => {
    mockWeatherDelay({
      data: {
        available: true,
        station: { station_id: "47765", station_name: "Example Station", note: "Nearest documented station." },
        wet_day_threshold_mm: 1.0,
        wet: { days: 8, samples: 240, avg_delay_sec: 95.5, avg_precip_mm: 12.3 },
        dry: { days: 22, samples: 660, avg_delay_sec: 60.2, avg_precip_mm: 0.0 },
        delta_sec: 35.3,
        low_confidence: false,
        ctx: CTX,
        disclaimer: "Observed rainfall, not a forecast or a causal claim.",
        attribution: "Source: example weather service",
      },
    });
    renderWithProviders(<WeatherDelayPanel aid={1} ctx={CTX as never} />);

    expect(screen.getByText(/Example Station/)).toBeInTheDocument();
    expect(screen.getByText("Nearest documented station.")).toBeInTheDocument();
    expect(screen.getByText("95.5s")).toBeInTheDocument();
    expect(screen.getByText("60.2s")).toBeInTheDocument();
    expect(screen.getByText("240")).toBeInTheDocument();
    expect(screen.getByText("660")).toBeInTheDocument();
    expect(screen.getByText(/\+35\.3\s*s/)).toBeInTheDocument();
    expect(screen.queryByText("Low confidence")).not.toBeInTheDocument();
    expect(screen.getByText("Observed rainfall, not a forecast or a causal claim.")).toBeInTheDocument();
    expect(screen.getByText("Source: example weather service")).toBeInTheDocument();
  });

  it("shows a skeleton while loading", () => {
    mockWeatherDelay({ isLoading: true });
    const { container } = renderWithProviders(<WeatherDelayPanel aid={1} ctx={CTX as never} />);
    expect(container.querySelector(".skeleton")).toBeInTheDocument();
  });
});
