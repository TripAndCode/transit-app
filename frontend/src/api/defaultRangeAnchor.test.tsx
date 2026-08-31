import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import * as hooks from "./hooks";
import { useDefaultRangeAnchor } from "./defaultRangeAnchor";
import { useAnonymousFilterPersistence } from "./anonymousFilterPersistence";
import { isoDaysAgo } from "./rangeContext";
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
});
