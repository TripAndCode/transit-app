import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import * as hooks from "./hooks";
import { useDefaultRangeAnchor } from "./defaultRangeAnchor";
import { isoDaysAgo } from "./rangeContext";
import type { Agency } from "./types";

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
