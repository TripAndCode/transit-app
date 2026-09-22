import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  useLiveTripProgress,
  useLiveTrips,
  useRouteStopProfile,
  useTodayRouteSummary,
} from "./hooks";

const mockApiGet = vi.fn();
vi.mock("./client", () => ({
  apiGet: (...args: unknown[]) => mockApiGet(...args),
  apiPost: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
}));

function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return ({ children }: { children: React.ReactNode }) =>
    createElement(QueryClientProvider, { client }, children);
}

// Every query MapTab keeps mounted while a route is selected. They render
// against one another on the same canvas — trip dots, the progress marker and
// the stop-delay gradient — so one of them holding a value the others have
// moved past reads as a bug in the map, not as a stale cache. `staleTime`
// alone never triggers a refetch, so polling has to be declared explicitly.
//
// What's asserted is that each one polls at all, not how fast. The right
// cadence differs per query — a position moves faster than a day-long
// average — and is argued at the hook; going silent is the defect.
const LIVE_MAP_HOOKS: Array<[string, () => unknown]> = [
  ["useLiveTrips", () => useLiveTrips(1)],
  ["useLiveTripProgress", () => useLiveTripProgress(1, "T1")],
  ["useTodayRouteSummary", () => useTodayRouteSummary(1)],
  ["useRouteStopProfile", () => useRouteStopProfile(1, "R1")],
];

describe("operations map live queries", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockApiGet.mockReset();
    mockApiGet.mockResolvedValue({});
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const LONGEST_ACCEPTABLE_INTERVAL_MS = 5 * 60_000;

  it.each(LIVE_MAP_HOOKS)("%s refetches on an interval", async (_name, hook) => {
    renderHook(hook, { wrapper: wrapper() });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mockApiGet).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(LONGEST_ACCEPTABLE_INTERVAL_MS);
    });
    expect(mockApiGet.mock.calls.length).toBeGreaterThan(1);
  });
});
