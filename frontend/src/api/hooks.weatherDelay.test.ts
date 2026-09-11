import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useWeatherDelay } from "./hooks";
import type { RangeCtx } from "./rangeContext";
import type { WeatherDelayResponse } from "./types";

const mockApiGet = vi.fn();
vi.mock("./client", () => ({
  apiGet: (...args: unknown[]) => mockApiGet(...args),
}));

const CTX: RangeCtx = {
  from: "2026-08-01",
  to: "2026-08-31",
  dow: "all",
  time_band: "all",
  service: "all",
  routes: [],
};

function setup(agencyId: number | null, ctx: RangeCtx, enabled: boolean) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return renderHook(() => useWeatherDelay(agencyId, ctx, enabled), {
    wrapper: ({ children }) => createElement(QueryClientProvider, { client: queryClient }, children),
  });
}

function unavailableResponse(): WeatherDelayResponse {
  return {
    available: false,
    station: null,
    wet_day_threshold_mm: 1.0,
    wet: { days: 0, samples: 0, avg_delay_sec: null, avg_precip_mm: null },
    dry: { days: 0, samples: 0, avg_delay_sec: null, avg_precip_mm: null },
    delta_sec: null,
    low_confidence: false,
    ctx: { from: CTX.from, to: CTX.to, dow: CTX.dow, time_band: CTX.time_band },
    disclaimer: "Observed rainfall, not a forecast or a causal claim.",
    attribution: "Source: example weather service",
  };
}

function availableResponse(): WeatherDelayResponse {
  return {
    available: true,
    station: { station_id: "47765", station_name: "Example Station", note: "Nearest documented station." },
    wet_day_threshold_mm: 1.0,
    wet: { days: 8, samples: 240, avg_delay_sec: 95.5, avg_precip_mm: 12.3 },
    dry: { days: 22, samples: 660, avg_delay_sec: 60.2, avg_precip_mm: 0.0 },
    delta_sec: 35.3,
    low_confidence: false,
    ctx: { from: CTX.from, to: CTX.to, dow: CTX.dow, time_band: CTX.time_band },
    disclaimer: "Observed rainfall, not a forecast or a causal claim.",
    attribution: "Source: example weather service",
  };
}

describe("useWeatherDelay", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockApiGet.mockReset();
  });

  it("fetches /weather_delay with the range context and returns an available comparison", async () => {
    mockApiGet.mockResolvedValue(availableResponse());
    const { result } = setup(1, CTX, true);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mockApiGet).toHaveBeenCalledWith(
      expect.stringMatching(/^\/api\/1\/weather_delay\?/),
      expect.anything(),
    );
    const [path] = mockApiGet.mock.calls[0] as [string];
    expect(path).toContain(`from=${CTX.from}`);
    expect(path).toContain(`to=${CTX.to}`);
    expect(result.current.data?.available).toBe(true);
    expect(result.current.data?.station?.station_id).toBe("47765");
    expect(result.current.data?.delta_sec).toBe(35.3);
  });

  it("surfaces available: false without treating it as an error", async () => {
    mockApiGet.mockResolvedValue(unavailableResponse());
    const { result } = setup(1, CTX, true);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.available).toBe(false);
    expect(result.current.data?.station).toBeNull();
    expect(result.current.data?.delta_sec).toBeNull();
  });

  it("does not fetch when disabled", () => {
    setup(1, CTX, false);
    expect(mockApiGet).not.toHaveBeenCalled();
  });

  it("does not fetch when there is no agency yet", () => {
    setup(null, CTX, true);
    expect(mockApiGet).not.toHaveBeenCalled();
  });
});
