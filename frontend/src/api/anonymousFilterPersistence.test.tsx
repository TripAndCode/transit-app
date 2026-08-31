import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, useNavigate, useSearchParams } from "react-router-dom";
import * as hooks from "./hooks";
import { useAnonymousFilterPersistence } from "./anonymousFilterPersistence";

const useSessionMock = vi.fn();
vi.mock("./auth", () => ({
  useSession: () => useSessionMock(),
}));


// Probe shares the same router context as the hook so it reactively sees
// whatever setSearchParams call the hook makes, mirroring
// defaultRangeAnchor.test.tsx's own probe pattern.
function Probe({ agencyId }: { agencyId: number | null }) {
  useAnonymousFilterPersistence(agencyId);
  const [params] = useSearchParams();
  return <div data-testid="params">{params.toString()}</div>;
}

function renderProbe(agencyId: number | null, initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Probe agencyId={agencyId} />
    </MemoryRouter>,
  );
}

// Mirrors AgencyPicker's `selectAgency`, which navigates to the same or a
// different agency without preserving any filter query params (unlike
// Sidebar's nav links, which always carry `ctxToQueryString`).
function NavigatingProbe({ agencyId }: { agencyId: number }) {
  useAnonymousFilterPersistence(agencyId);
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(`/agencies/${agencyId}/overview`)}>
      reselect
    </button>
  );
}

// Simulates a mid-session UI action that explicitly removes one param from
// an otherwise-unchanged URL (e.g. clearing just the DOW filter while a
// date range/time-band stay selected) — a real re-render of the SAME
// component instance (so `restoredFor`'s ref state persists across it),
// unlike remounting with a different `initialEntries`.
function ClearParamProbe({ agencyId, paramToClear }: { agencyId: number; paramToClear: string }) {
  useAnonymousFilterPersistence(agencyId);
  const [, setParams] = useSearchParams();
  return (
    <button
      type="button"
      onClick={() =>
        setParams((prev) => {
          const next = new URLSearchParams(prev);
          next.delete(paramToClear);
          return next;
        })
      }
    >
      clear {paramToClear}
    </button>
  );
}

describe("useAnonymousFilterPersistence", () => {
  beforeEach(() => {
    localStorage.clear();
    useSessionMock.mockReturnValue({ data: null, isLoading: false });
    // The hook now also reads useAgencies (to defer to useDefaultRangeAnchor
    // via the shared computeAnchorRange — see anonymousFilterPersistence.ts's
    // docstring); mock it as "not loaded" so computeAnchorRange is always a
    // no-op here and these tests keep exercising restore/persist in
    // isolation. The dedicated interaction coverage lives in
    // defaultRangeAnchor.test.tsx.
    vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: undefined, isPending: true } as never);
  });
  afterEach(() => vi.restoreAllMocks());

  it("does nothing while logged in", () => {
    useSessionMock.mockReturnValue({ data: { user_id: 1 }, isLoading: false });
    localStorage.setItem(
      "transit.lastFilter.1",
      JSON.stringify({ dow: "weekend" }),
    );
    renderProbe(1, "/agencies/1/overview");
    expect(screen.getByTestId("params")).toHaveTextContent("");
  });

  it("does nothing while the session is still loading", () => {
    useSessionMock.mockReturnValue({ data: null, isLoading: true });
    localStorage.setItem(
      "transit.lastFilter.1",
      JSON.stringify({ dow: "weekend" }),
    );
    renderProbe(1, "/agencies/1/overview");
    expect(screen.getByTestId("params")).toHaveTextContent("");
  });

  it("restores a stored filter on a fresh visit with no explicit filter params", () => {
    localStorage.setItem(
      "transit.lastFilter.1",
      JSON.stringify({
        from: "2026-01-01",
        to: "2026-01-31",
        dow: "weekend",
        time_band: "evening",
        service: "all",
        routes: ["A1", "B2"],
      }),
    );
    renderProbe(1, "/agencies/1/overview");
    const params = new URLSearchParams(screen.getByTestId("params").textContent ?? "");
    expect(params.get("from")).toBe("2026-01-01");
    expect(params.get("to")).toBe("2026-01-31");
    expect(params.get("dow")).toBe("weekend");
    expect(params.get("time_band")).toBe("evening");
    expect(params.get("routes")).toBe("A1,B2");
  });

  it("does not restore when the URL already has an explicit filter param", () => {
    localStorage.setItem(
      "transit.lastFilter.1",
      JSON.stringify({ dow: "weekend" }),
    );
    renderProbe(1, "/agencies/1/overview?dow=weekday");
    expect(screen.getByTestId("params")).toHaveTextContent("dow=weekday");
  });

  it("persists the current filter to localStorage for later restoration", () => {
    renderProbe(1, "/agencies/1/overview?dow=weekend&time_band=night");
    const stored = JSON.parse(localStorage.getItem("transit.lastFilter.1") ?? "{}");
    expect(stored.dow).toBe("weekend");
    expect(stored.time_band).toBe("night");
  });

  it("scopes storage per agency", () => {
    localStorage.setItem(
      "transit.lastFilter.1",
      JSON.stringify({ dow: "weekend" }),
    );
    renderProbe(2, "/agencies/2/overview");
    expect(screen.getByTestId("params")).toHaveTextContent("");
  });

  it("does nothing when agencyId is null", () => {
    renderProbe(null, "/");
    expect(screen.getByTestId("params")).toHaveTextContent("");
  });

  it("does not wipe a previously persisted filter when re-navigating to the same agency with no filter params", () => {
    render(
      <MemoryRouter initialEntries={["/agencies/1/overview?dow=weekend&time_band=evening"]}>
        <NavigatingProbe agencyId={1} />
      </MemoryRouter>,
    );
    expect(JSON.parse(localStorage.getItem("transit.lastFilter.1") ?? "{}")).toMatchObject({
      dow: "weekend",
      time_band: "evening",
    });

    fireEvent.click(screen.getByText("reselect"));

    const stored = JSON.parse(localStorage.getItem("transit.lastFilter.1") ?? "{}");
    expect(stored.dow).toBe("weekend");
    expect(stored.time_band).toBe("evening");
  });

  it("a mid-session explicit clear of one field is not resurrected by a later persist write", () => {
    render(
      <MemoryRouter initialEntries={["/agencies/1/overview?from=2026-01-01&to=2026-01-07&dow=weekend&time_band=evening"]}>
        <ClearParamProbe agencyId={1} paramToClear="dow" />
      </MemoryRouter>,
    );
    // First render already persisted the full filter and marked agency 1 as
    // no longer a first attempt (isFirstAttemptForAgency only ever true on
    // the very first render this hook processes for an agency).
    let stored = JSON.parse(localStorage.getItem("transit.lastFilter.1") ?? "{}");
    expect(stored.dow).toBe("weekend");

    fireEvent.click(screen.getByText("clear dow"));

    // The merge that backfills a field missing from the current params must
    // NOT run here (isFirstAttemptForAgency is now false) -- otherwise the
    // user's explicit clear would be silently undone by the stale value
    // still sitting in storage from the render just above.
    stored = JSON.parse(localStorage.getItem("transit.lastFilter.1") ?? "{}");
    expect(stored.dow).toBeUndefined();
    expect(stored.time_band).toBe("evening");
    expect(stored.from).toBe("2026-01-01");
    expect(stored.to).toBe("2026-01-07");
  });

  it("a mid-session explicit clear of routes is not resurrected by a later persist write", () => {
    render(
      <MemoryRouter initialEntries={["/agencies/1/overview?from=2026-01-01&to=2026-01-07&routes=A1,B2"]}>
        <ClearParamProbe agencyId={1} paramToClear="routes" />
      </MemoryRouter>,
    );
    let stored = JSON.parse(localStorage.getItem("transit.lastFilter.1") ?? "{}");
    expect(stored.routes).toEqual(["A1", "B2"]);

    fireEvent.click(screen.getByText("clear routes"));

    stored = JSON.parse(localStorage.getItem("transit.lastFilter.1") ?? "{}");
    expect(stored.routes).toBeUndefined();
    expect(stored.from).toBe("2026-01-01");
  });

  it("still fills in a missing field from storage on a genuine first attempt with a partial explicit URL (e.g. a deep link)", () => {
    // Not the anchor-handoff case (no from/to at all here), but still a
    // first-ever effect run for this agency this session -- the merge is
    // gated on `isFirstAttemptForAgency`, not on whether the anchor fired,
    // so any first-attempt partial URL benefits from the same "remember
    // what I was looking at" fill-in the restore branch already provides
    // for a fully-empty URL.
    localStorage.setItem(
      "transit.lastFilter.1",
      JSON.stringify({ time_band: "evening", service: "weekday" }),
    );
    renderProbe(1, "/agencies/1/overview?dow=weekend");
    const stored = JSON.parse(localStorage.getItem("transit.lastFilter.1") ?? "{}");
    expect(stored.dow).toBe("weekend"); // current param always wins
    expect(stored.time_band).toBe("evening"); // filled in, absent from the URL
    expect(stored.service).toBe("weekday"); // filled in, absent from the URL
  });
});
