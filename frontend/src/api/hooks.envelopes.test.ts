import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRoutes } from "./hooks";
import type { Route, RoutesResponse } from "./types";

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
