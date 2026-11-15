import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import type { DwellRunPayload } from "../../api/types";
import { WhyAnalyses } from "./WhyAnalyses";

const mockApiGet = vi.fn();
vi.mock("../../api/client", async (orig) => ({
  ...(await orig<typeof import("../../api/client")>()),
  apiGet: (...args: unknown[]) => mockApiGet(...args),
}));

const DWELL: DwellRunPayload = { available: true, time_band_supported: true, unavailable_reason: null, last_checked: null, routes: [] };
const WEATHER = {
  available: true,
  station: { station_id: "1", station_name: "Example Station", note: null },
  wet_day_threshold_mm: 1,
  wet: { days: 6, samples: 900, avg_delay_sec: 80, avg_precip_mm: 7 },
  dry: { days: 20, samples: 3000, avg_delay_sec: 60, avg_precip_mm: 0 },
  delta_sec: 20,
  low_confidence: false,
  ctx: {},
  disclaimer: "d",
  attribution: "a",
};

function serve(weatherEnabled: boolean) {
  mockApiGet.mockImplementation(async (path: string) => {
    const url = String(path);
    if (url.startsWith("/api/config")) return { auth_enabled: false, local_admin_enabled: false, weather_enabled: weatherEnabled };
    if (url.includes("/weather_delay")) return WEATHER;
    if (url.includes("/headway_quality")) return { rows: [], ctx: {}, unavailable_reason: "no_high_frequency_routes", last_checked: null };
    if (url.includes("/session") || url.includes("/auth")) return { role: null };
    return [];
  });
}

function mount() {
  const router = createMemoryRouter(
    [{ path: "/agencies/:agencyId/why", element: <WhyAnalyses aid={1} payload={DWELL} /> }],
    { initialEntries: ["/agencies/1/why?from=2026-09-01&to=2026-09-07"] },
  );
  renderWithProviders(<RouterProvider router={router} />);
}

const urls = () => mockApiGet.mock.calls.map((c) => String(c[0]));

beforeEach(() => mockApiGet.mockReset());

describe("Why's weather panel and the weather flag", () => {
  it("makes no weather request and mounts no panel while the flag is off", async () => {
    serve(false);
    mount();
    await waitFor(() => expect(urls().some((u) => u.startsWith("/api/config"))).toBe(true));
    await waitFor(() => expect(urls().some((u) => u.includes("/headway_quality"))).toBe(true));
    expect(urls().filter((u) => u.includes("/weather_delay"))).toEqual([]);
    expect(screen.queryByText("Rain vs. dry average delay")).not.toBeInTheDocument();
  });

  it("fetches and mounts the panel once the flag is on", async () => {
    serve(true);
    mount();
    expect(await screen.findByText("Station: Example Station")).toBeInTheDocument();
    expect(urls().some((u) => u.includes("/weather_delay"))).toBe(true);
  });
});
