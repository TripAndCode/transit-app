import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, fireEvent, act, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useParams } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { OnboardingGate } from "./OnboardingGate";
import * as hooks from "../api/hooks";
import type { Agency } from "../api/types";
import { formatDate } from "../utils/format";

function agency(over: Partial<Agency>): Agency {
  return { agency_id: 1, agency_name: "Agency", feed_url: "", static_url: null, latest_data_date: null, ...over };
}

function LandingProbe() {
  const { agencyId } = useParams();
  return <div>landed:{agencyId}:pulse</div>;
}

function renderGate() {
  return renderWithProviders(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<OnboardingGate />} />
        <Route path="/agencies/:agencyId/pulse" element={<LandingProbe />} />
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
  beforeEach(() => {
    localStorage.clear();
  });
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
    expect(screen.getByText("landed:5:pulse")).toBeTruthy();
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
    expect(screen.getByText("landed:2:pulse")).toBeTruthy();
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
    expect(screen.queryByText("landed:2:pulse")).toBeNull();
    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(screen.getByText("landed:2:pulse")).toBeTruthy();
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

  it("leaves a fresh signed-out browser on the picker; RequireAuth owns sending it to /welcome", () => {
    localStorage.clear();
    mockAgencies([agency({ agency_id: 1, agency_name: "First" }), agency({ agency_id: 2, agency_name: "Second" })]);
    renderGate();
    expect(screen.getByText("First")).toBeTruthy();
    expect(screen.queryByText("landed:welcome")).toBeNull();
  });

  it("says which agencies have data, and through when", () => {
    mockAgencies([
      agency({ agency_id: 1, agency_name: "Collected", latest_data_date: "2026-10-03" }),
      agency({ agency_id: 2, agency_name: "Never collected" }),
    ]);
    renderGate();
    const collected = screen.getByRole("button", { name: /^Collected/ });
    expect(collected).toHaveTextContent(`Data through ${formatDate("2026-10-03")}`);
    expect(screen.getByRole("button", { name: /^Never collected/ })).toHaveTextContent("No data collected yet");
  });

  it("keeps the date in one piece, so a narrow card wraps before it and never inside it", () => {
    mockAgencies([
      agency({ agency_id: 1, agency_name: "Collected", latest_data_date: "2026-09-10" }),
      agency({ agency_id: 2, agency_name: "Never collected" }),
    ]);
    renderGate();
    const caption = screen.getByRole("button", { name: /^Collected/ }).lastElementChild;
    expect(caption?.textContent).toBe("Data through Sep\u00a010,\u00a02026");
  });

  it("lists the agencies with data first", () => {
    mockAgencies([
      agency({ agency_id: 1, agency_name: "Empty one" }),
      agency({ agency_id: 2, agency_name: "Full one", latest_data_date: "2026-10-03" }),
      agency({ agency_id: 3, agency_name: "Empty two" }),
    ]);
    renderGate();
    const names = screen.getAllByRole("button").map((b) => b.textContent ?? "").filter((n) => /one|two/.test(n));
    expect(names[0]).toMatch(/^Full one/);
  });

  it("offers a search once the list is long, and none for a short one", () => {
    mockAgencies(Array.from({ length: 9 }, (_, i) => agency({ agency_id: i + 1, agency_name: `Agency ${i + 1}` })));
    renderGate();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "agency 9" } });
    expect(screen.getAllByRole("button", { name: /^Agency/ })).toHaveLength(1);
  });

  it("says when a search matches no agency", () => {
    mockAgencies(Array.from({ length: 9 }, (_, i) => agency({ agency_id: i + 1, agency_name: `Agency ${i + 1}` })));
    renderGate();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "zzz" } });
    expect(screen.getByRole("status")).toHaveTextContent("No agency matches. Try another name.");
  });

  it("has no search for a short list", () => {
    mockAgencies([agency({ agency_id: 1, agency_name: "A" }), agency({ agency_id: 2, agency_name: "B" })]);
    renderGate();
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
  });

  it("lets a visitor switch language before choosing", () => {
    mockAgencies([agency({ agency_id: 1, agency_name: "A" }), agency({ agency_id: 2, agency_name: "B" })]);
    renderGate();
    expect(screen.getByRole("group", { name: "Language" })).toBeInTheDocument();
  });
});
