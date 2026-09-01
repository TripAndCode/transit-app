import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import * as hooks from "./hooks";
import { useAnonymousFilterPersistence } from "./anonymousFilterPersistence";
import type { Agency } from "./types";

const useSessionMock = vi.fn();
vi.mock("./auth", () => ({
  useSession: () => useSessionMock(),
}));

function agency(partial: Partial<Agency> = {}): Agency {
  return { agency_id: 1, agency_name: "Test", feed_url: "http://x", static_url: null, latest_data_date: null, ...partial };
}

function Probe({ agencyId }: { agencyId: number | null }) {
  useAnonymousFilterPersistence(agencyId);
  const [params] = useSearchParams();
  return <div data-testid="params">{params.toString()}</div>;
}

/**
 * Isolated coverage of useAnonymousFilterPersistence's defer-to-anchor check
 * itself (as opposed to defaultRangeAnchor.test.tsx's end-to-end coverage of
 * the real interaction between both hooks): confirms the restore is
 * genuinely withheld when computeAnchorRange would apply, using the same
 * already-cached agencies data useDefaultRangeAnchor itself reads — not
 * just a coincidence of test setup.
 */
describe("useAnonymousFilterPersistence defers to computeAnchorRange", () => {
  beforeEach(() => {
    localStorage.clear();
    useSessionMock.mockReturnValue({ data: null, isLoading: false });
  });
  afterEach(() => vi.restoreAllMocks());

  it("withholds the restore when the agency's data is stale enough that useDefaultRangeAnchor would also rewrite from/to", () => {
    vi.spyOn(hooks, "useAgencies").mockReturnValue({
      data: [agency({ latest_data_date: "2026-05-01" })],
      isPending: false,
    } as never);
    localStorage.setItem("transit.lastFilter.1", JSON.stringify({ dow: "weekend" }));
    render(
      <MemoryRouter initialEntries={["/agencies/1/overview"]}>
        <Probe agencyId={1} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("params")).toHaveTextContent("");
  });

  it("withholds the restore while agencies is still pending, even before any anchor-relevant params exist", () => {
    // Cold page load (fresh tab/reload/deep link): no router prefetch
    // guarantees useAgencies() is already warm, so this hook's effect can
    // fire on the very first render while `agencies` is still pending.
    // computeAnchorRange can't distinguish that from "no anchor needed" by
    // its return value alone, so this hook must wait for `agencies` to
    // resolve before acting at all -- otherwise it could restore a stored
    // from/to ahead of useDefaultRangeAnchor ever getting a chance to
    // override it (a stored from/to already in the URL makes every later
    // computeAnchorRange call return null for "range already present", not
    // "no rewrite needed").
    const agenciesSpy = vi.spyOn(hooks, "useAgencies");
    agenciesSpy.mockReturnValue({ data: undefined, isPending: true } as never);
    localStorage.setItem(
      "transit.lastFilter.1",
      JSON.stringify({ from: "2020-01-01", to: "2020-01-07" }),
    );
    const { rerender } = render(
      <MemoryRouter initialEntries={["/agencies/1/overview"]}>
        <Probe agencyId={1} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("params")).toHaveTextContent("");

    agenciesSpy.mockReturnValue({
      data: [agency({ latest_data_date: null })],
      isPending: false,
    } as never);
    rerender(
      <MemoryRouter initialEntries={["/agencies/1/overview"]}>
        <Probe agencyId={1} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("params")).toHaveTextContent("from=2020-01-01&to=2020-01-07");
  });
});
