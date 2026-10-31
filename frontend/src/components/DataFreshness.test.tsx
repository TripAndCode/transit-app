import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/renderWithProviders";
import { DataFreshness } from "./DataFreshness";
import * as hooks from "../api/hooks";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DataFreshness", () => {
  it("states the latest reading in JST in its panel, the zone the chip names", async () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: { latest_captured_at: "2026-09-29T00:59:00Z", date: "2026-09-29", routes: [], raw_samples: 10, clamp_count: 0 },
    } as never);
    renderWithProviders(<DataFreshness agencyId={1} through="2026-09-28" />);
    await userEvent.click(screen.getByRole("button"));
    const panel = screen.getByRole("region", { name: "How fresh the data is" });
    expect(panel.textContent?.replace(/\s/g, " ")).toContain("Sep 29, 2026, 9:59 AM JST");
  });

  it("says the figures already leave out what was set aside, whenever anything was, however little", async () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: { latest_captured_at: "2026-10-23T14:59:00Z", date: "2026-10-23", routes: [], raw_samples: 19_345_155, clamp_count: 112 },
    } as never);
    renderWithProviders(<DataFreshness agencyId={8} through="2026-10-23" />);
    await userEvent.click(screen.getByRole("button"));
    const panel = screen.getByRole("region", { name: "How fresh the data is" });
    expect(panel).toHaveTextContent("Set aside as implausible112");
    expect(panel).toHaveTextContent("The figures shown already exclude them.");
  });

  it("says nothing about exclusions when nothing was set aside", async () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: { latest_captured_at: "2026-10-23T14:59:00Z", date: "2026-10-23", routes: [], raw_samples: 397_693, clamp_count: 0 },
    } as never);
    renderWithProviders(<DataFreshness agencyId={1} through="2026-10-23" />);
    await userEvent.click(screen.getByRole("button"));
    const panel = screen.getByRole("region", { name: "How fresh the data is" });
    expect(panel).not.toHaveTextContent("already exclude");
  });
});
