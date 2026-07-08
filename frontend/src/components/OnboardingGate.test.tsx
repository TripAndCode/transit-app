import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, fireEvent, act } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useParams } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { OnboardingGate } from "./OnboardingGate";
import * as hooks from "../api/hooks";
import type { Agency } from "../api/types";

function agency(over: Partial<Agency>): Agency {
  return { agency_id: 1, agency_name: "Agency", feed_url: "", static_url: null, ...over };
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

function mockAgencies(data: Agency[] | undefined, isLoading = false) {
  vi.spyOn(hooks, "useAgencies").mockReturnValue({ data, isLoading } as never);
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
});
