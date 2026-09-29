import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/renderWithProviders";
import * as client from "../../api/client";
import { ActivitySection } from "./UserDrawerSections";

afterEach(() => vi.restoreAllMocks());

describe("ActivitySection", () => {
  it("lists each endpoint's requests and errors for the window", async () => {
    vi.spyOn(client, "apiGet").mockResolvedValue([
      { route: "/api/{agency_id}/overview/summary", method: "GET", agency_id: 1, requests: 5, errors: 1 },
    ]);
    renderWithProviders(<ActivitySection uid={9} />);
    expect(await screen.findByText("GET /api/{agency_id}/overview/summary")).toBeTruthy();
    expect(screen.getByText("5")).toBeTruthy();
    expect(screen.getByText("1")).toBeTruthy();
    expect(client.apiGet).toHaveBeenCalledWith("/api/admin/users/9/activity?days=30", expect.anything());
  });

  it("says so when nothing was recorded", async () => {
    vi.spyOn(client, "apiGet").mockResolvedValue([]);
    renderWithProviders(<ActivitySection uid={9} />);
    expect(await screen.findByText("No recorded requests in the last 30 days.")).toBeTruthy();
  });
});
