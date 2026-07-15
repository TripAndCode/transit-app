import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, fireEvent, act, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useParams } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { OnboardingGate } from "./OnboardingGate";
import * as hooks from "../api/hooks";
import type { Agency } from "../api/types";

function agency(over: Partial<Agency>): Agency {
  return { agency_id: 1, agency_name: "Agency", feed_url: "", static_url: null, latest_data_date: null, ...over };
}

function MapProbe() {
  const { agencyId } = useParams();
  return <div>landed:{agencyId}</div>;
}

function renderGate() {
  return renderWithProviders(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<OnboardingGate />} />
        <Route path="/agencies/:agencyId/map" element={<MapProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

function mockAgencies(
  data: Agency[] | undefined,
  isLoading = false,
  over: { isError?: boolean; error?: unknown; refetch?: () => void } = {},
) {
  vi.spyOn(hooks, "useAgencies").mockReturnValue({
    data,
    isLoading,
    isError: over.isError ?? false,
    error: over.error ?? null,
    refetch: over.refetch ?? vi.fn(),
  } as never);
}

describe("OnboardingGate", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("shows the loading placeholder while agencies load", () => {
    mockAgencies(undefined, true);
    renderGate();
    expect(screen.getByText("Loading agencies...")).toBeTruthy();
    expect(screen.queryByText(/^landed:/)).toBeNull();
  });

  it("shows an error banner with retry when the agencies fetch fails", () => {
    const refetch = vi.fn();
    mockAgencies(undefined, false, { isError: true, error: new Error("network down"), refetch });
    renderGate();
    expect(screen.getByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByText("Retry"));
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("shows a distinct empty-state message when zero agencies are configured", () => {
    mockAgencies([]);
    renderGate();
    expect(screen.getByText("No agencies are configured yet.")).toBeTruthy();
    expect(screen.queryByText(/^landed:/)).toBeNull();
  });

  it("navigates immediately for a single agency, no overlay", () => {
    mockAgencies([agency({ agency_id: 5, agency_name: "Solo" })]);
    renderGate();
    expect(screen.getByText("landed:5")).toBeTruthy();
    expect(screen.queryByText("Solo")).toBeNull();
  });

  it("shows the overlay for multiple agencies with no stored preference", () => {
    mockAgencies([agency({ agency_id: 1, agency_name: "First" }), agency({ agency_id: 2, agency_name: "Second" })]);
    renderGate();
    expect(screen.queryByText(/^landed:/)).toBeNull();
    expect(screen.getByText("First")).toBeTruthy();
    expect(screen.getByText("Second")).toBeTruthy();
  });

  it("navigates immediately when a valid preference is stored", () => {
    localStorage.setItem("transit.lastAgency", "2");
    mockAgencies([agency({ agency_id: 1, agency_name: "First" }), agency({ agency_id: 2, agency_name: "Second" })]);
    renderGate();
    expect(screen.getByText("landed:2")).toBeTruthy();
  });

  it("falls through to the overlay when the stored preference no longer exists", () => {
    localStorage.setItem("transit.lastAgency", "999");
    mockAgencies([agency({ agency_id: 1, agency_name: "First" }), agency({ agency_id: 2, agency_name: "Second" })]);
    renderGate();
    expect(screen.queryByText(/^landed:/)).toBeNull();
    expect(screen.getByText("First")).toBeTruthy();
  });

  it("clicking a card persists the choice and navigates", () => {
    vi.useFakeTimers();
    mockAgencies([agency({ agency_id: 1, agency_name: "First" }), agency({ agency_id: 2, agency_name: "Second" })]);
    renderGate();
    fireEvent.click(screen.getByText("Second"));
    expect(localStorage.getItem("transit.lastAgency")).toBe("2");
    expect(screen.queryByText("landed:2")).toBeNull();
    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(screen.getByText("landed:2")).toBeTruthy();
    vi.useRealTimers();
  });

  it("shows the checkmark badge only on the clicked card, only after the click", () => {
    vi.useFakeTimers();
    mockAgencies([agency({ agency_id: 1, agency_name: "First" }), agency({ agency_id: 2, agency_name: "Second" })]);
    renderGate();
    expect(screen.queryByTestId("agency-check-badge")).toBeNull();

    fireEvent.click(screen.getByText("Second"));

    const badges = screen.getAllByTestId("agency-check-badge");
    expect(badges).toHaveLength(1);
    const secondCard = screen.getByText("Second").closest("button");
    expect(secondCard).not.toBeNull();
    expect(within(secondCard as HTMLElement).getByTestId("agency-check-badge")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(250);
    });
    vi.useRealTimers();
  });
});
