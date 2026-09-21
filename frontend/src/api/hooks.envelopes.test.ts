import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRoutes, useSuggestion } from "./hooks";
import type { Route, RoutesResponse, SuggestionEnvelope } from "./types";

const mockApiGet = vi.fn();
vi.mock("./client", () => ({
  apiGet: (...args: unknown[]) => mockApiGet(...args),
}));

function wrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return ({ children }: { children: React.ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children);
}

const ROUTE: Route = {
  route_id: "国道・古川線(1021)",
  route_short_name: "1021",
  route_long_name: "国道・古川線",
  route_code: "1021",
  trip_headsigns: ["古川行"],
};

describe("useRoutes (object envelope)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockApiGet.mockReset();
  });

  it("unwraps the server's { rows } envelope so consumers still see a plain array", async () => {
    mockApiGet.mockResolvedValue({ rows: [ROUTE] } satisfies RoutesResponse);
    const { result } = renderHook(() => useRoutes(1), { wrapper: wrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mockApiGet).toHaveBeenCalledWith("/api/1/routes", expect.anything());
    expect(result.current.data).toEqual([ROUTE]);
  });

  it("surfaces an empty catalogue as an empty array, not undefined", async () => {
    mockApiGet.mockResolvedValue({ rows: [] } satisfies RoutesResponse);
    const { result } = renderHook(() => useRoutes(1), { wrapper: wrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });
});

describe("useSuggestion (object envelope)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockApiGet.mockReset();
  });

  it("unwraps the server's { suggestion } envelope", async () => {
    const suggestion = {
      report_type: "on_time",
      route_code: "BAD",
      reason_text: "遅れが目立ちます",
      severity: "notable",
      from_date: "2026-08-25",
      to_date: "2026-08-31",
    } as const;
    mockApiGet.mockResolvedValue({ suggestion } satisfies SuggestionEnvelope);
    const { result } = renderHook(() => useSuggestion(1, []), { wrapper: wrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(suggestion);
  });

  it("maps the 'no signal' envelope to null rather than an object with a null field", async () => {
    mockApiGet.mockResolvedValue({ suggestion: null } satisfies SuggestionEnvelope);
    const { result } = renderHook(() => useSuggestion(1, ["on_time:BAD"]), { wrapper: wrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
    expect(mockApiGet).toHaveBeenCalledWith(
      "/api/1/reports/suggest?exclude=on_time%3ABAD",
      expect.anything(),
    );
  });
});
