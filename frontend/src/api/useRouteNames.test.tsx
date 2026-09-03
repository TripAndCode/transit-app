import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { useRouteNames } from "./useRouteNames";
import * as hooks from "./hooks";
import type { Route } from "./types";

void i18n.changeLanguage("en");

function mockRoutes(data: Route[] | undefined, isLoading = false) {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data, isLoading } as never);
}

// JSX-consumer coverage for useRouteNames' `data`/`isLoading` passthrough —
// complementary to useRouteNames.test.ts's plain-`.ts` unit tests of the
// `format()` fallback order (route_short_name -> route_long_name -> route_id
// -> bare-code fallback), which don't need JSX.
function Probe({ agencyId }: { agencyId: number }) {
  const { data, isLoading, format } = useRouteNames(agencyId);
  if (isLoading) return <span>loading</span>;
  return (
    <span data-testid="probe">
      {format("39061")}|{data.size}
    </span>
  );
}

function setup(agencyId = 1) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <Probe agencyId={agencyId} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("useRouteNames (JSX consumer)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reflects useRoutes' isLoading state before data arrives", () => {
    mockRoutes(undefined, true);
    setup();
    expect(screen.getByText("loading")).toBeInTheDocument();
  });

  it("exposes a route_code -> name Map sized to the agency's route list", () => {
    mockRoutes([
      { route_id: "T50線(39061)", route_short_name: "T50", route_long_name: "石江・新城線", route_code: "39061", trip_headsigns: [] },
      { route_id: "国道・古川線(1021)", route_short_name: "国道", route_long_name: "国道・古川線", route_code: "1021", trip_headsigns: [] },
    ]);
    setup();
    expect(screen.getByTestId("probe")).toHaveTextContent("T50 (39061)|2");
  });
});
