import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, fireEvent, act, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useParams } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { OnboardingGate } from "./OnboardingGate";
import * as hooks from "../api/hooks";
import * as config from "../api/config";
import * as auth from "../api/auth";
import type { Agency } from "../api/types";

const HIROSHIMA_FEED = "https://ajt-mobusta-gtfs.mcapps.jp/realtime/9/trip_updates.bin";
const AOMORI_FEED = "https://aomoricitybus.com/TripUpdate.pb";

function agency(over: Partial<Agency>): Agency {
  return { agency_id: 1, agency_name: "Agency", feed_url: "", static_url: null, latest_data_date: null, ...over };
}

/** A signed-out guest on a deployment with sign-in available but not
 *  required, unless `visitor` says otherwise; `session: undefined` is a
 *  session still loading. */
function mockVisitor(visitor: { auth_enabled?: boolean; login_required?: boolean; session?: object | null } = {}) {
  const { auth_enabled = true, login_required = false } = visitor;
  vi.spyOn(config, "useConfig").mockReturnValue({
    data: { auth_enabled, local_admin_enabled: false, login_required },
  } as never);
  vi.spyOn(auth, "useSession").mockReturnValue({ data: "session" in visitor ? visitor.session : null } as never);
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
    mockVisitor();
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

  it("names the newest data day once, above the cards, instead of on each card", () => {
    mockAgencies([
      agency({ agency_id: 1, agency_name: "First", latest_data_date: "2026-10-23" }),
      agency({ agency_id: 2, agency_name: "Second", latest_data_date: "2026-10-22" }),
      agency({ agency_id: 3, agency_name: "Never collected" }),
    ]);
    renderGate();
    expect(screen.getAllByText(/Data through/)).toHaveLength(1);
    expect(screen.getByText(/Data through/)).toHaveTextContent("Data through Oct 23");
    expect(screen.getByRole("button", { name: /^First/ })).not.toHaveTextContent(/Data through|No updates/);
    expect(screen.getByRole("button", { name: /^Never collected/ })).toHaveTextContent("No data collected yet");
  });

  it("notes only the agency whose data stopped well behind the others", () => {
    mockAgencies([
      agency({ agency_id: 1, agency_name: "Stopped", latest_data_date: "2026-10-14" }),
      agency({ agency_id: 2, agency_name: "Current", latest_data_date: "2026-10-23" }),
      agency({ agency_id: 3, agency_name: "A day late", latest_data_date: "2026-10-22" }),
    ]);
    renderGate();
    expect(screen.getByRole("button", { name: /^Stopped/ })).toHaveTextContent("No updates after Oct 14");
    expect(screen.getByRole("button", { name: /^Current/ })).not.toHaveTextContent("No updates");
    expect(screen.getByRole("button", { name: /^A day late/ })).not.toHaveTextContent("No updates");
  });

  it("keeps the stopped note when a search leaves only that agency", () => {
    mockAgencies([
      agency({ agency_id: 1, agency_name: "Stopped", latest_data_date: "2026-10-14" }),
      ...Array.from({ length: 8 }, (_, i) =>
        agency({ agency_id: i + 2, agency_name: `Agency ${i + 2}`, latest_data_date: "2026-10-23" }),
      ),
    ]);
    renderGate();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "stopped" } });
    expect(screen.getByRole("button", { name: /^Stopped/ })).toHaveTextContent("No updates after Oct 14");
  });

  it("groups the agencies by prefecture, with how many each holds", () => {
    mockAgencies([
      agency({ agency_id: 1, agency_name: "Aomori City Bus", feed_url: AOMORI_FEED, latest_data_date: "2026-10-23" }),
      agency({ agency_id: 8, agency_name: "Hiroden", feed_url: HIROSHIMA_FEED, latest_data_date: "2026-10-23" }),
      agency({ agency_id: 9, agency_name: "Hiroshima Bus", feed_url: HIROSHIMA_FEED, latest_data_date: "2026-10-23" }),
    ]);
    renderGate();
    const groups = screen.getAllByRole("heading", { level: 2 });
    expect(groups.map((h) => h.textContent)).toEqual(["Hiroshima 2 agencies", "Aomori 1 agency"]);
    const hiroshima = groups[0].closest("section") as HTMLElement;
    expect(within(hiroshima).getAllByRole("button").map((b) => b.textContent)).toEqual(["Hiroden", "Hiroshima Bus"]);
  });

  it("lists an agency from an unknown source under Other", () => {
    mockAgencies([
      agency({ agency_id: 9, agency_name: "Hiroshima Bus", feed_url: HIROSHIMA_FEED }),
      agency({ agency_id: 70, agency_name: "Newcomer", feed_url: "https://example.com/rt.pb" }),
    ]);
    renderGate();
    const other = screen.getByRole("heading", { level: 2, name: /^Other/ }).closest("section") as HTMLElement;
    expect(within(other).getByRole("button", { name: /^Newcomer/ })).toBeInTheDocument();
  });

  it("lists the agencies with data first, then by name", () => {
    mockAgencies([
      agency({ agency_id: 1, agency_name: "Empty one" }),
      agency({ agency_id: 2, agency_name: "Full two", latest_data_date: "2026-10-03" }),
      agency({ agency_id: 3, agency_name: "Full one", latest_data_date: "2026-10-03" }),
    ]);
    renderGate();
    const names = screen.getAllByRole("button").map((b) => b.textContent ?? "").filter((n) => /one|two/.test(n));
    expect(names).toEqual(["Full one", "Full two", "Empty oneNo data collected yet"]);
  });

  it("explains the app, and offers help, about and login to a signed-out guest", () => {
    mockAgencies([agency({ agency_id: 1, agency_name: "A" }), agency({ agency_id: 2, agency_name: "B" })]);
    renderGate();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Choose an agency");
    expect(screen.getByText(/Look up bus delays/)).toHaveTextContent("No sign-in needed.");
    expect(screen.getByRole("link", { name: "Help" })).toHaveAttribute("href", "/help");
    expect(screen.getByRole("link", { name: "About this app" })).toHaveAttribute("href", "/welcome");
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it.each([
    ["a signed-in user", { session: { user_id: 1 } }],
    ["a deployment without sign-in", { auth_enabled: false }],
  ])("offers no login link to %s", (_, visitor) => {
    mockVisitor(visitor);
    mockAgencies([agency({ agency_id: 1, agency_name: "A" }), agency({ agency_id: 2, agency_name: "B" })]);
    renderGate();
    expect(screen.queryByRole("link", { name: "Sign in" })).toBeNull();
    expect(screen.getByRole("link", { name: "Help" })).toBeInTheDocument();
  });

  it.each([
    ["a signed-in user", { session: { user_id: 1 } }],
    ["a deployment that requires login", { login_required: true, session: { user_id: 1 } }],
    ["a session still loading", { session: undefined }],
  ])("does not say login is unneeded to %s", (_, visitor) => {
    mockVisitor(visitor);
    mockAgencies([agency({ agency_id: 1, agency_name: "A" }), agency({ agency_id: 2, agency_name: "B" })]);
    renderGate();
    expect(screen.queryByText(/No sign-in needed/)).toBeNull();
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

  // jsdom has no layout, so this pins the page's inline styles rather than
  // its rendered position: the page scrolls as a whole, and its content
  // starts at the top instead of being centred, which would push a long
  // list's head above the scroll origin.
  it("scrolls as one page whose content starts at the top", () => {
    mockAgencies([agency({ agency_id: 1, agency_name: "A" }), agency({ agency_id: 2, agency_name: "B" })]);
    renderGate();
    const page = screen.getByTestId("agency-directory");
    expect(page).toContainElement(screen.getByRole("heading", { level: 1 }));
    expect(page.style.overflowY).toBe("auto");
    expect(page.style.alignItems).toBe("");
    expect(page.style.justifyContent).toBe("");
  });

  it("lets a visitor switch language before choosing", () => {
    mockAgencies([agency({ agency_id: 1, agency_name: "A" }), agency({ agency_id: 2, agency_name: "B" })]);
    renderGate();
    expect(screen.getByRole("group", { name: "Language" })).toBeInTheDocument();
  });
});
