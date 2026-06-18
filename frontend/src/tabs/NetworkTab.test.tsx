import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { NetworkTab } from "./NetworkTab";
import * as hooks from "../api/hooks";
import type { NetworkAgencyRow } from "../api/types";

function row(over: Partial<NetworkAgencyRow>): NetworkAgencyRow {
  return {
    agency_id: 1, agency_name: "A", avg_delay_min: 5, on_time_pct: 90,
    samples: 100, raw_samples: 1000, clamp_count: 5, clamp_pct: 0.5, is_stale: false,
    data_from: "2026-04-01", data_to: "2026-04-02", ...over,
  };
}

function renderTab() {
  renderWithProviders(
    <MemoryRouter initialEntries={["/network"]}>
      <NetworkTab />
    </MemoryRouter>,
  );
}

describe("NetworkTab", () => {
  it("renders agencies in given order with stale chip, no-data dash, clamp %", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07",
        agencies: [
          row({ agency_id: 1, agency_name: "Hiroden", avg_delay_min: 10, on_time_pct: 50, clamp_pct: 0.14 }),
          row({ agency_id: 2, agency_name: "HiroBus", avg_delay_min: 4, on_time_pct: 88, clamp_pct: 10, is_stale: true, data_from: "2026-04-03", data_to: "2026-04-05" }),
          row({ agency_id: 3, agency_name: "Aomori", avg_delay_min: null, on_time_pct: null, samples: 0, clamp_pct: null, data_from: null, data_to: null }),
        ],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    const names = screen.getAllByText(/Hiroden|HiroBus|Aomori/).map((n) => n.textContent);
    expect(names).toEqual(["Hiroden", "HiroBus", "Aomori"]);
    expect(screen.getByText("10.0")).toBeInTheDocument();
    expect(screen.getByText("10.00%")).toBeInTheDocument();
    expect(screen.getByText("Behind")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(1);
    // clamp dot boundary: present only for HiroBus (10%), absent for
    // Hiroden (0.14 < 1) and Aomori (null).
    expect(screen.getAllByTestId("clamp-dot")).toHaveLength(1);
    expect(screen.getByText("2026-04-01 – 2026-04-02")).toBeInTheDocument();
    expect(screen.getByText("no data in range")).toBeInTheDocument();
    expect(screen.getByText("How to read this")).toBeInTheDocument();
  });

  it("renders the empty message and no table when there are no agencies", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: { from: "2026-04-01", to: "2026-04-07", agencies: [] },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    expect(screen.getByText("No agencies.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows a skeleton (no table) while pending", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: undefined, isPending: true, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows the error banner with a retry on error", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: undefined, isPending: false, error: new Error("boom"), refetch: vi.fn(),
    } as never);
    renderTab();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});
