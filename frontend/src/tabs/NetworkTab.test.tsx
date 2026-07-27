import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { NetworkTab } from "./NetworkTab";
import i18n from "../i18n";
import * as hooks from "../api/hooks";
import type { NetworkAgencyRow } from "../api/types";

function row(over: Partial<NetworkAgencyRow>): NetworkAgencyRow {
  return {
    agency_id: 1, agency_name: "A", avg_delay_min: 5, on_time_pct: 90,
    samples: 100, raw_samples: 1000, clamp_count: 5, clamp_pct: 0.5, is_stale: false,
    data_from: "2026-04-01", data_to: "2026-04-02", ...over,
  };
}

function renderTab(agencyId = "1") {
  renderWithProviders(
    <MemoryRouter initialEntries={[`/agencies/${agencyId}/network`]}>
      <Routes>
        <Route path="/agencies/:agencyId/network" element={<NetworkTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("NetworkTab", () => {
  beforeEach(async () => await i18n.changeLanguage("en"));

  it("renders agency cards in given order with stale badge, no-data dash, clamp % dot", () => {
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
    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.getByText("#2")).toBeInTheDocument();
    expect(screen.getByText("#3")).toBeInTheDocument();
    expect(screen.getByText(/\+10\.0/)).toBeInTheDocument();
    expect(screen.getByText("50.0%")).toBeInTheDocument(); // Hiroden's on-time %
    expect(screen.getByText("10.00%")).toBeInTheDocument(); // HiroBus's clamp % (secondary line, shown since 10% > 1% threshold)
    expect(screen.getByText("Behind")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(1);
    // clamp dot boundary: present only for HiroBus (10%), absent for
    // Hiroden (0.14 < 1) and Aomori (null) — no secondary line at all for
    // Hiroden since neither clamp nor stale triggers.
    expect(screen.getAllByTestId("clamp-dot")).toHaveLength(1);
    expect(screen.getByText("2026-04-01 – 2026-04-02")).toBeInTheDocument();
    expect(screen.getByText("no data in range")).toBeInTheDocument();
    expect(screen.getByText("How to read this")).toBeInTheDocument();
  });

  it("links each agency name to its overview, carrying the current range", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07",
        agencies: [row({ agency_id: 7, agency_name: "Hiroden" })],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/7/network?from=2026-04-01&to=2026-04-07"]}>
        <Routes>
          <Route path="/agencies/:agencyId/network" element={<NetworkTab />} />
        </Routes>
      </MemoryRouter>,
    );
    const link = screen.getByRole("link", { name: "Hiroden" });
    expect(link).toHaveAttribute(
      "href",
      "/agencies/7/overview?from=2026-04-01&to=2026-04-07",
    );
    expect(link).toHaveAttribute("title", "View Hiroden overview");
  });

  it("renders the empty message and no agency cards when there are no agencies", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: { from: "2026-04-01", to: "2026-04-07", agencies: [] },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    expect(screen.getByText("No agencies.")).toBeInTheDocument();
    expect(screen.queryByTestId("network-card-list")).not.toBeInTheDocument();
  });

  it("shows a skeleton (no cards) while pending", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: undefined, isPending: true, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    expect(screen.queryByTestId("network-card-list")).not.toBeInTheDocument();
  });

  it("shows the error banner with a retry on error", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: undefined, isPending: false, error: new Error("boom"), refetch: vi.fn(),
    } as never);
    renderTab();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("shows a 'you' badge and highlights the card matching the current agencyId in the URL", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07",
        agencies: [
          row({ agency_id: 1, agency_name: "Hiroden" }),
          row({ agency_id: 2, agency_name: "HiroBus" }),
        ],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab("2"); // viewing agency 2 (HiroBus)
    const badges = screen.getAllByTestId("you-badge");
    expect(badges).toHaveLength(1);
    // the badge sits inside HiroBus's card, not Hiroden's
    const hiroBusCard = screen.getByText("HiroBus").closest(".network-card");
    expect(hiroBusCard).toContainElement(badges[0]);
  });

  it("shows no 'you' badge when there is no agencyId in the URL", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07",
        agencies: [row({ agency_id: 1, agency_name: "Hiroden" })],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={["/network"]}>
        <NetworkTab />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("you-badge")).not.toBeInTheDocument();
  });

  it("renders the coverage-range separator via the locale-aware key, not a hardcoded en-dash", async () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07",
        agencies: [row({ agency_id: 1, agency_name: "Hiroden", data_from: "2026-04-01", data_to: "2026-04-02" })],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    await i18n.changeLanguage("ja");
    renderTab();
    expect(screen.getByText("2026-04-01 〜 2026-04-02")).toBeInTheDocument();
  });

  it("sets both range date inputs' lang attribute to the active UI language", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: { from: "2026-04-01", to: "2026-04-07", agencies: [] },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    const inputs = document.querySelectorAll("input[type='date']");
    expect(inputs.length).toBe(2);
    inputs.forEach((el) => expect(el.getAttribute("lang")).toBe("en"));
  });
});
