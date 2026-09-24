import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import * as hooks from "./hooks";
import { latestDataWindow, useDefaultRangeAnchor, useJumpToLatestDataRange } from "./defaultRangeAnchor";
import { useAnonymousFilterPersistence } from "./anonymousFilterPersistence";
import { DEFAULT_RANGE_DAYS, isoDaysAgo, isoDaysBefore } from "./rangeContext";
import type { Agency } from "./types";

const useSessionMock = vi.fn();
vi.mock("./auth", () => ({
  useSession: () => useSessionMock(),
}));

function agency(partial: Partial<Agency> = {}): Agency {
  return { agency_id: 1, agency_name: "Test", feed_url: "http://x", static_url: null, latest_data_date: null, ...partial };
}

// Probe shares the same router context as the hook, so it reactively sees
// whatever useDefaultRangeAnchor's setSearchParams call writes — reading
// window.location wouldn't work here, MemoryRouter never touches it.
function Probe({ agencyId }: { agencyId: number | null }) {
  useDefaultRangeAnchor(agencyId);
  const [params] = useSearchParams();
  return <div data-testid="params">{params.toString()}</div>;
}

function renderAnchor(agencyId: number | null, initialPath: string) {
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Probe agencyId={agencyId} />
    </MemoryRouter>,
  );
}

describe("useDefaultRangeAnchor", () => {
  afterEach(() => vi.restoreAllMocks());

  it("does nothing when the URL already has an explicit from/to", () => {
    vi.spyOn(hooks, "useAgencies").mockReturnValue({
      data: [agency({ latest_data_date: "2026-01-01" })],
      isPending: false,
    } as never);
    renderAnchor(1, "/agencies/1/overview?from=2030-01-01&to=2030-01-07");
    expect(screen.getByTestId("params")).toHaveTextContent("from=2030-01-01&to=2030-01-07");
  });

  it("does nothing when the agency has no data at all", () => {
    vi.spyOn(hooks, "useAgencies").mockReturnValue({
      data: [agency({ latest_data_date: null })],
      isPending: false,
    } as never);
    renderAnchor(1, "/agencies/1/overview");
    expect(screen.getByTestId("params")).toHaveTextContent("");
  });

  it("does nothing when latest_data_date already falls inside the default window", () => {
    vi.spyOn(hooks, "useAgencies").mockReturnValue({
      data: [agency({ latest_data_date: isoDaysAgo(5) })],
      isPending: false,
    } as never);
    renderAnchor(1, "/agencies/1/overview");
    expect(screen.getByTestId("params")).toHaveTextContent("");
  });

  it("rewrites from/to when latest_data_date is outside the default window", () => {
    vi.spyOn(hooks, "useAgencies").mockReturnValue({
      data: [agency({ latest_data_date: "2026-05-01" })],
      isPending: false,
    } as never);
    renderAnchor(1, "/agencies/1/overview");
    const params = new URLSearchParams(screen.getByTestId("params").textContent ?? "");
    expect(params.get("to")).toBe("2026-05-01");
    expect(params.get("from")).toBe("2026-04-02");
  });
});

describe("useDefaultRangeAnchor + useAnonymousFilterPersistence interaction", () => {
  afterEach(() => vi.restoreAllMocks());
  beforeEach(() => {
    localStorage.clear();
    useSessionMock.mockReturnValue({ data: null, isLoading: false });
  });

  // Both hooks' effects fire from the same render and each independently
  // calls setSearchParams on a fresh visit; without
  // useAnonymousFilterPersistence deferring to computeAnchorRange, whichever
  // hook's effect happened to run second (an accident of declaration order,
  // not a deliberate precedence) would silently clobber the other's
  // rewrite, since both build their update from the same stale
  // pre-navigation `searchParams` snapshot.
  function CombinedProbe({ agencyId }: { agencyId: number | null }) {
    useDefaultRangeAnchor(agencyId);
    useAnonymousFilterPersistence(agencyId);
    const [params] = useSearchParams();
    return <div data-testid="params">{params.toString()}</div>;
  }

  it("the freshly-anchored non-empty window wins over a stale stored filter, not whichever hook happens to run last", () => {
    vi.spyOn(hooks, "useAgencies").mockReturnValue({
      data: [agency({ latest_data_date: "2026-05-01" })],
      isPending: false,
    } as never);
    localStorage.setItem(
      "transit.lastFilter.1",
      JSON.stringify({ from: "2020-01-01", to: "2020-01-07" }),
    );
    render(
      <MemoryRouter initialEntries={["/agencies/1/overview"]}>
        <CombinedProbe agencyId={1} />
      </MemoryRouter>,
    );
    const params = new URLSearchParams(screen.getByTestId("params").textContent ?? "");
    expect(params.get("to")).toBe("2026-05-01");
    expect(params.get("from")).toBe("2026-04-02");
  });

  it("still restores a stored filter when the anchor has nothing to do (agency's data is current)", () => {
    vi.spyOn(hooks, "useAgencies").mockReturnValue({
      data: [agency({ latest_data_date: isoDaysAgo(5) })],
      isPending: false,
    } as never);
    localStorage.setItem(
      "transit.lastFilter.1",
      JSON.stringify({ dow: "weekend", time_band: "evening" }),
    );
    render(
      <MemoryRouter initialEntries={["/agencies/1/overview"]}>
        <CombinedProbe agencyId={1} />
      </MemoryRouter>,
    );
    const params = new URLSearchParams(screen.getByTestId("params").textContent ?? "");
    expect(params.get("dow")).toBe("weekend");
    expect(params.get("time_band")).toBe("evening");
  });

  it("a previously-stored dow/time_band (no stored from/to) survives a visit where the anchor rewrites from/to", () => {
    vi.spyOn(hooks, "useAgencies").mockReturnValue({
      data: [agency({ latest_data_date: "2026-05-01" })],
      isPending: false,
    } as never);
    localStorage.setItem(
      "transit.lastFilter.1",
      JSON.stringify({ dow: "weekend", time_band: "evening" }),
    );
    render(
      <MemoryRouter initialEntries={["/agencies/1/overview"]}>
        <CombinedProbe agencyId={1} />
      </MemoryRouter>,
    );
    // The anchor wins the URL's from/to (same outcome as the first test in
    // this block) ...
    const params = new URLSearchParams(screen.getByTestId("params").textContent ?? "");
    expect(params.get("to")).toBe("2026-05-01");
    expect(params.get("from")).toBe("2026-04-02");
    // ... but the persist branch that then runs (once the anchor's rewrite
    // has landed and computeAnchorRange stops deferring) must not silently
    // drop dow/time_band just because this particular render's URL only
    // carries the anchor's own from/to -- those fields never conflicted
    // with anything the anchor did.
    const stored = JSON.parse(localStorage.getItem("transit.lastFilter.1") ?? "{}");
    expect(stored.dow).toBe("weekend");
    expect(stored.time_band).toBe("evening");
  });
});

describe("latestDataWindow", () => {
  it("returns null when the agency id or agencies list is unavailable", () => {
    expect(latestDataWindow(null, [agency()])).toBeNull();
    expect(latestDataWindow(1, undefined)).toBeNull();
  });

  it("returns null when the agency has no data at all", () => {
    expect(latestDataWindow(1, [agency({ latest_data_date: null })])).toBeNull();
  });

  it("returns a DEFAULT_RANGE_DAYS window ending at latest_data_date, regardless of today's date", () => {
    const range = latestDataWindow(1, [agency({ latest_data_date: "2026-05-01" })]);
    expect(range).toEqual({ from: isoDaysBefore("2026-05-01", DEFAULT_RANGE_DAYS - 1), to: "2026-05-01" });
  });

  it("returns the same window even when latest_data_date already falls inside today's default window", () => {
    // Unlike computeAnchorRange, this is a manual "take me to real data"
    // recovery -- it must not defer to "today's window already covers it"
    // when the reason the current view is empty has nothing to do with the
    // date range (e.g. a route/service filter with no matching rows).
    const recent = isoDaysAgo(2);
    const range = latestDataWindow(1, [agency({ latest_data_date: recent })]);
    expect(range).toEqual({ from: isoDaysBefore(recent, DEFAULT_RANGE_DAYS - 1), to: recent });
  });
});

describe("useJumpToLatestDataRange", () => {
  afterEach(() => vi.restoreAllMocks());

  function JumpProbe({ agencyId }: { agencyId: number | null }) {
    const jump = useJumpToLatestDataRange(agencyId);
    const [params] = useSearchParams();
    return (
      <div>
        <div data-testid="params">{params.toString()}</div>
        {jump && (
          <button type="button" onClick={jump}>
            jump
          </button>
        )}
      </div>
    );
  }

  it("returns null (renders no control) when the agency has no latest data date", async () => {
    vi.spyOn(hooks, "useAgencies").mockReturnValue({
      data: [agency({ latest_data_date: null })],
      isPending: false,
    } as never);
    render(
      <MemoryRouter initialEntries={["/agencies/1/analysis?from=2020-01-01&to=2020-01-07"]}>
        <JumpProbe agencyId={1} />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("overwrites an existing explicit from/to with the agency's latest-data window on click", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    vi.spyOn(hooks, "useAgencies").mockReturnValue({
      data: [agency({ latest_data_date: "2026-05-01" })],
      isPending: false,
    } as never);
    render(
      <MemoryRouter initialEntries={["/agencies/1/analysis?from=2020-01-01&to=2020-01-07&dow=weekend"]}>
        <JumpProbe agencyId={1} />
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole("button"));
    const params = new URLSearchParams(screen.getByTestId("params").textContent ?? "");
    expect(params.get("to")).toBe("2026-05-01");
    expect(params.get("from")).toBe(isoDaysBefore("2026-05-01", DEFAULT_RANGE_DAYS - 1));
    // Unrelated params survive the rewrite.
    expect(params.get("dow")).toBe("weekend");
  });
});
