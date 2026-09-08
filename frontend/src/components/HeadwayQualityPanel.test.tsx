import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { HeadwayQualityPanel } from "./HeadwayQualityPanel";
import * as hooks from "../api/hooks";
import type { HeadwayQualityResponse } from "../api/types";

const CTX = { from: "2026-04-01", to: "2026-04-02", dow: "all", time_band: "all", service: "all", routes: [] } as const;

function mockRoutes() {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
}

function mockHeadwayQuality(result: Partial<{ data: HeadwayQualityResponse; isLoading: boolean; error: unknown }>) {
  vi.spyOn(hooks, "useHeadwayQuality").mockReturnValue({
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

describe("HeadwayQualityPanel", () => {
  it("renders a row per high-frequency route with formatted metrics", () => {
    mockRoutes();
    mockHeadwayQuality({
      data: {
        rows: [
          { route_code: "100", ewt_sec: 183.75, cov: 0.875, long_gap_rate: 0.5, samples: 2 },
          { route_code: "200", ewt_sec: 0, cov: 0, long_gap_rate: 0, samples: 4 },
        ],
        ctx: CTX,
      },
    });
    renderWithProviders(<HeadwayQualityPanel aid={1} ctx={CTX as never} />);

    expect(screen.getByText("Route 100")).toBeInTheDocument();
    expect(screen.getByText("Route 200")).toBeInTheDocument();
    // 183.75s rounds to +3 min via the shared signed-minute formatting.
    expect(screen.getByText("+3 min")).toBeInTheDocument();
    expect(screen.getByText("0.88")).toBeInTheDocument();
    expect(screen.getByText("50.0%")).toBeInTheDocument();
  });

  it("shows the empty state when there are zero high-frequency routes", () => {
    mockRoutes();
    mockHeadwayQuality({ data: { rows: [], ctx: CTX } });
    renderWithProviders(<HeadwayQualityPanel aid={1} ctx={CTX as never} />);
    expect(screen.getByText(/no high-frequency route/i)).toBeInTheDocument();
  });

  it("shows a skeleton while loading", () => {
    mockRoutes();
    mockHeadwayQuality({ isLoading: true });
    const { container } = renderWithProviders(<HeadwayQualityPanel aid={1} ctx={CTX as never} />);
    expect(container.querySelector(".skeleton")).toBeInTheDocument();
  });
});
