import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { NoticeStack } from "./NoticeStack";
import * as hooks from "../api/hooks";
import type { RouteSummaryResponse } from "../api/types";

function staleSummary(): RouteSummaryResponse {
  return {
    latest_captured_at: new Date(Date.now() - 48 * 60 * 60 * 1000).toISOString(),
    date: "2026-06-09",
    routes: [],
    raw_samples: 100,
    clamp_count: 0,
  };
}

function stack(focused: boolean) {
  return (
    <MemoryRouter initialEntries={["/9"]}>
      <Routes>
        <Route path="/:agencyId" element={<NoticeStack focused={focused} />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("NoticeStack", () => {
  it("keeps a banner's dismissal through a visit to a focused tab", async () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({ data: staleSummary() } as never);
    const { rerender } = renderWithProviders(stack(false));
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();

    rerender(stack(true));
    rerender(stack(false));
    expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
  });

  it("shows no banner on a focused tab", () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({ data: staleSummary() } as never);
    renderWithProviders(stack(true));
    expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
  });
});
