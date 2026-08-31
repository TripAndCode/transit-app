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
});
