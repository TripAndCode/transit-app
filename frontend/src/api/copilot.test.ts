import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCopilotInsight, DEBOUNCE_MS } from "./copilot";
import type { RangeCtx } from "./rangeContext";

const mockApiPost = vi.fn();
vi.mock("./client", () => ({
  apiGet: vi.fn(),
  apiPost: (...args: unknown[]) => mockApiPost(...args),
}));

const CTX: RangeCtx = {
  from: "2026-08-01",
  to: "2026-08-31",
  dow: "all",
  time_band: "all",
  service: "all",
  routes: [],
};

function setup(
  agencyId: number | null,
  tab: string | null,
  filters: RangeCtx,
  viewPayload: unknown,
) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return renderHook(() => useCopilotInsight(agencyId, tab, filters, viewPayload), {
    wrapper: ({ children }) => createElement(QueryClientProvider, { client: queryClient }, children),
  });
}

describe("useCopilotInsight", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    mockApiPost.mockReset();
  });

  it("debounces the very first request the same as a later key change, instead of firing immediately on mount", async () => {
    mockApiPost.mockResolvedValue({ text: "x", cite: "c", low_confidence: false });
    setup(1, "overview", CTX, { some: "payload" });

    // Flush microtasks without advancing real time past DEBOUNCE_MS -- a
    // bug that skips the debounce on the initial key would already have
    // fired the POST by this point.
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(mockApiPost).not.toHaveBeenCalled();

    await act(() => vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 50));
    expect(mockApiPost).toHaveBeenCalledTimes(1);
  });
});
