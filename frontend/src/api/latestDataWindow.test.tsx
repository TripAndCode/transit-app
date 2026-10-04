import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import * as hooks from "./hooks";
import { latestDataWindow, useJumpToLatestDataRange } from "./latestDataWindow";
import { DEFAULT_RANGE_DAYS, isoDaysAgo, isoDaysBefore } from "./scope";
import type { Agency } from "./types";

function agency(partial: Partial<Agency> = {}): Agency {
  return { agency_id: 1, agency_name: "Test", feed_url: "http://x", static_url: null, latest_data_date: null, ...partial };
}

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
    // A view can be empty for reasons other than its dates (a route or
    // service filter with no matching rows), so the recovery never declines
    // because the default window already covers the data.
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
