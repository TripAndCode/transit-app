import type { ReactElement } from "react";
import { describe, it, expect, vi, beforeAll, afterAll, afterEach } from "vitest";
import { render as rtlRender, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import i18n from "../i18n";
import * as hooks from "../api/hooks";
import { PeakHourModal } from "./PeakHourModal";
import type { PeakHourBreakdown } from "../api/types";

/** The modal names routes from the agency's static routes, so it renders
 *  under an agency's path with a query client. */
function render(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return rtlRender(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/agencies/1/pulse"]}>
        <Routes>
          <Route path="/agencies/:agencyId/pulse" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

const mockBreakdown: PeakHourBreakdown = {
  hour: 8,
  dow: 5,
  routes: [
    { route_code: "K31", avg_min: 6.5, samples: 50 },
    { route_code: "K37", avg_min: 5.2, samples: 30 },
  ],
};

describe("PeakHourModal", () => {
  it("renders route list", () => {
    render(
      <PeakHourModal
        data={mockBreakdown}
        loading={false}
        onClose={() => {}}
      />
    );
    expect(screen.getByText("Route K31")).toBeInTheDocument();
    expect(screen.getByText("Route K37")).toBeInTheDocument();
  });

  it("names each route by its compact label, never its route_id", () => {
    vi.spyOn(hooks, "useRoutes").mockReturnValue({
      data: [{ route_id: "K31", route_code: "K31", route_short_name: "70-3", route_long_name: "あさひが丘線", trip_headsigns: [] }],
      isLoading: false,
    } as never);
    render(<PeakHourModal data={mockBreakdown} loading={false} onClose={() => {}} />);
    const label = screen.getByText("70-3").closest(".route-label")!;
    expect(label).toHaveTextContent(/^70-3 あさひが丘線$/);
    expect(label).toHaveAttribute("title", "K31");
  });

  it("lists each route once by its label, never printing a raw service id", () => {
    // A response cached from before the breakdown pooled by route can still
    // carry the GTFS service_id each row matched.
    const stale = {
      hour: 8,
      dow: 5,
      routes: [{ route_code: "K31", service_type: "11_平日(共通)", avg_min: 6.5, samples: 50 }], // i18n-ignore: GTFS service id
    } as unknown as PeakHourBreakdown;
    render(<PeakHourModal data={stale} loading={false} onClose={() => {}} />);
    expect(screen.getByText("Route K31")).toBeInTheDocument();
    expect(screen.queryByText(/11_/)).toBeNull();
    expect(screen.getByTestId("peak-hour-modal-bar-K31")).toBeInTheDocument();
  });

  it("shows the request's failure and a retry instead of an empty body", () => {
    const onRetry = vi.fn();
    render(
      <PeakHourModal data={null} loading={false} error={new Error("boom")} onRetry={onRetry} onClose={() => {}} />
    );
    fireEvent.click(screen.getByRole("button", { name: i18n.t("common.retry") }));
    expect(onRetry).toHaveBeenCalledOnce();
    expect(screen.queryByTestId("peak-hour-modal-empty")).not.toBeInTheDocument();
  });

  it("calls onClose when backdrop clicked", () => {
    const onClose = vi.fn();
    render(
      <PeakHourModal data={mockBreakdown} loading={false} onClose={onClose} />
    );
    fireEvent.click(screen.getByRole("presentation"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("renders via the shared accessible Modal (aria-modal dialog)", () => {
    render(
      <PeakHourModal data={mockBreakdown} loading={false} onClose={() => {}} />
    );
    expect(screen.getByRole("dialog")).toHaveAttribute("aria-modal", "true");
  });

  it("shows empty state when no routes", () => {
    const empty: PeakHourBreakdown = { hour: 3, dow: null, routes: [] };
    render(<PeakHourModal data={empty} loading={false} onClose={() => {}} />);
    expect(screen.getByTestId("peak-hour-modal-empty")).toBeInTheDocument();
  });

  it("shows spinner when loading", () => {
    render(<PeakHourModal data={null} loading={true} onClose={() => {}} />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("clamps the bar width to 0 instead of a negative value for an early-running route", () => {
    // maxAvg is set by K31 (a delayed route); K37 is early-running (negative
    // avg_min), which divides out to a negative CSS width percentage unless clamped.
    const mixed: PeakHourBreakdown = {
      hour: 8,
      dow: 5,
      routes: [
        { route_code: "K31", avg_min: 6.5, samples: 50 },
        { route_code: "K37", avg_min: -3.2, samples: 30 },
      ],
    };
    render(<PeakHourModal data={mixed} loading={false} onClose={() => {}} />);
    const bar = screen.getByTestId("peak-hour-modal-bar-K37");
    expect(bar.style.width).toBe("0%");
    // The label still shows a single, correctly-signed negative value (not
    // a double sign like "+-3.2min") — exact match, since an unanchored
    // substring match can't distinguish the two.
    expect(screen.getByText("-3.2min")).toBeInTheDocument();
    // The positive route's sign is still explicit, not omitted.
    expect(screen.getByText("+6.5min")).toBeInTheDocument();
  });

  it("shows a delay that rounds to zero as +0.0min, never -0.0min", () => {
    const tiny: PeakHourBreakdown = {
      hour: 8,
      dow: 5,
      routes: [{ route_code: "K31", avg_min: -0.04, samples: 50 }],
    };
    render(<PeakHourModal data={tiny} loading={false} onClose={() => {}} />);
    expect(screen.getByText("+0.0min")).toBeInTheDocument();
    expect(screen.queryByText("-0.0min")).not.toBeInTheDocument();
  });

  it("does not render a NaN width when every route has avg_min of exactly 0", () => {
    // Without a `|| 1` fallback maxAvg would be 0 in this case, so
    // (0/0)*100 is NaN, and Math.max(NaN, 0) is NaN (not 0) since any
    // comparison with NaN is false in JS.
    const allZero: PeakHourBreakdown = {
      hour: 8,
      dow: 5,
      routes: [
        { route_code: "K31", avg_min: 0, samples: 50 },
        { route_code: "K37", avg_min: 0, samples: 30 },
      ],
    };
    render(<PeakHourModal data={allZero} loading={false} onClose={() => {}} />);
    expect(screen.getByTestId("peak-hour-modal-bar-K31").style.width).toBe("0%");
    expect(screen.getByTestId("peak-hour-modal-bar-K37").style.width).toBe("0%");
  });

  it("caps the bar width at 100 instead of overflowing when every route is early-running", () => {
    // maxAvg is itself negative here (least-negative of the two), so a more
    // negative avg_min divided by a negative maxAvg is a ratio > 1 — the
    // one-sided clamp would let this exceed 100%.
    const allNegative: PeakHourBreakdown = {
      hour: 8,
      dow: 5,
      routes: [
        { route_code: "K31", avg_min: -0.5, samples: 50 },
        { route_code: "K37", avg_min: -6.5, samples: 30 },
      ],
    };
    render(<PeakHourModal data={allNegative} loading={false} onClose={() => {}} />);
    expect(screen.getByTestId("peak-hour-modal-bar-K37").style.width).toBe("100%");
  });

  describe("title grammar in Japanese", () => {
    beforeAll(async () => await i18n.changeLanguage("ja"));
    afterAll(async () => await i18n.changeLanguage("en"));

    it("does not render the broken '全曜' when dow is null (the ribbon's every-hour-click path)", () => {
      const allDays: PeakHourBreakdown = { hour: 10, dow: null, routes: mockBreakdown.routes };
      render(<PeakHourModal data={allDays} loading={false} onClose={() => {}} />);
      expect(screen.queryByText(/全曜/)).toBeNull();
    });

    it("renders a specific day's title correctly", () => {
      render(<PeakHourModal data={mockBreakdown} loading={false} onClose={() => {}} />);
      expect(screen.getByText(/金曜 8時台/)).toBeInTheDocument();
    });
  });
});
