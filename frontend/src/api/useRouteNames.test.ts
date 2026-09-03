import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { useRouteNames } from "./useRouteNames";
import * as hooks from "./hooks";
import type { Route } from "./types";

void i18n.changeLanguage("en");

function mockRoutes(data: Route[]) {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data, isLoading: false } as never);
}

function setup() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return renderHook(() => useRouteNames(1), {
    wrapper: ({ children }) =>
      createElement(
        QueryClientProvider,
        { client: queryClient },
        createElement(I18nextProvider, { i18n }, children),
      ),
  });
}

describe("useRouteNames", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("prefers route_short_name when present", () => {
    mockRoutes([
      { route_id: "T50線(39061)", route_short_name: "T50", route_long_name: "石江・新城線", route_code: "39061", trip_headsigns: [] },
    ]);
    const { result } = setup();
    expect(result.current.format("39061")).toBe("T50 (39061)");
  });

  it("falls back to route_long_name when route_short_name is blank", () => {
    // Real GTFS data allows either name field to be empty; only route_id is
    // guaranteed non-blank. Preferring route_long_name here over the raw
    // route_id keeps the displayed label human-readable instead of showing
    // the internal id (which already embeds the code a second time).
    mockRoutes([
      { route_id: "国道・古川線(1021)", route_short_name: "", route_long_name: "国道・古川線", route_code: "1021", trip_headsigns: [] },
    ]);
    const { result } = setup();
    expect(result.current.format("1021")).toBe("国道・古川線 (1021)");
  });

  it("falls back to route_id when both name fields are blank", () => {
    mockRoutes([
      { route_id: "国道・古川線(1021)", route_short_name: "", route_long_name: "", route_code: "1021", trip_headsigns: [] },
    ]);
    const { result } = setup();
    expect(result.current.format("1021")).toBe("国道・古川線(1021) (1021)");
  });

  it("shows the bare-code fallback when no static route matches", () => {
    // Genuine miss (e.g. a static-data gap for that route_code): no name
    // field is available at all, so the localized "Route {{code}}" template
    // is the correct, honest fallback.
    mockRoutes([
      { route_id: "T50線(39061)", route_short_name: "T50", route_long_name: "石江・新城線", route_code: "39061", trip_headsigns: [] },
    ]);
    const { result } = setup();
    expect(result.current.format("53011")).toBe("Route 53011");
  });

  it("returns an em dash for a null/undefined route_code", () => {
    mockRoutes([]);
    const { result } = setup();
    expect(result.current.format(null)).toBe("—");
    expect(result.current.format(undefined)).toBe("—");
  });
});
