import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { InsightPanel } from "./InsightPanel";
import i18n from "../i18n";
import * as hooks from "../api/hooks";

function renderPanel(agencyId = "1") {
  return renderWithProviders(
    <MemoryRouter initialEntries={[`/agencies/${agencyId}/analysis/trend`]}>
      <Routes>
        <Route path="/agencies/:agencyId/analysis/:reportType" element={<InsightPanel />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("InsightPanel", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
    localStorage.clear();
    sessionStorage.clear();
  });

  it("renders nothing when the feature flag is off", () => {
    const spy = vi.spyOn(hooks, "useSuggestion").mockReturnValue({
      data: { report_type: "trend", route_code: "R1", reason_text: "test reason", severity: "notable" },
      isPending: false,
      error: null,
    } as never);
    const { container } = renderPanel();
    expect(container.textContent).toBe("");
    // The flag being off must gate the hook call itself, not just the render —
    // otherwise every unopted-in visit still fires a live suggest request.
    expect(spy).toHaveBeenCalledWith(null, []);
  });

  it("shows the suggestion and navigates on click when flag is on", () => {
    localStorage.setItem("transit.insightPanelEnabled", "1");
    vi.spyOn(hooks, "useSuggestion").mockReturnValue({
      data: { report_type: "trend", route_code: "R1", reason_text: "Route R1 is anomalous", severity: "notable" },
      isPending: false,
      error: null,
    } as never);
    renderPanel();
    expect(screen.getByText("Route R1 is anomalous")).toBeTruthy();
    fireEvent.click(screen.getByText("View"));
    // sessionStorage now records this pathway as shown, per the dedup design.
    expect(sessionStorage.getItem("transit.insightPanelSeen")).toContain("trend:R1");
  });

  it("shows the calm no-signal message when the endpoint returns null", () => {
    localStorage.setItem("transit.insightPanelEnabled", "1");
    vi.spyOn(hooks, "useSuggestion").mockReturnValue({
      data: null,
      isPending: false,
      error: null,
    } as never);
    renderPanel();
    expect(screen.getByText("No notable signal right now.")).toBeTruthy();
  });

  it("collapses on click and persists the preference", () => {
    localStorage.setItem("transit.insightPanelEnabled", "1");
    vi.spyOn(hooks, "useSuggestion").mockReturnValue({
      data: { report_type: "trend", route_code: "R1", reason_text: "Route R1 is anomalous", severity: "notable" },
      isPending: false,
      error: null,
    } as never);
    renderPanel();
    fireEvent.click(screen.getByLabelText("Collapse insight panel"));
    expect(screen.queryByText("Route R1 is anomalous")).toBeNull();
    expect(localStorage.getItem("transit.insightPanelCollapsed")).toBe("1");
  });
});
