import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { InsightPanel } from "./InsightPanel";
import i18n from "../i18n";
import * as hooks from "../api/hooks";

function renderPanel(agencyId = "1") {
  const router = createMemoryRouter(
    [{ path: "/agencies/:agencyId/*", element: <InsightPanel /> }],
    { initialEntries: [`/agencies/${agencyId}/time?report=trend`] },
  );
  const result = renderWithProviders(<RouterProvider router={router} />);
  return Object.assign(result, { router });
}

describe("InsightPanel", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("renders nothing when the feature flag is explicitly off", () => {
    // Explicit "0" simulates a real deploy where a user opted out, or a
    // test environment where import.meta.env.DEV happens to be true (as it
    // is under Vitest) -- an explicit preference always wins over the
    // dev-build default, so this is the only way to exercise "off" here.
    localStorage.setItem("transit.insightPanelEnabled", "0");
    const spy = vi.spyOn(hooks, "useSuggestion").mockReturnValue({
      data: {
        report_type: "trend",
        route_code: "R1",
        reason_text: "test reason",
        severity: "notable",
        from_date: "2026-08-15",
        to_date: "2026-08-15",
      },
      isPending: false,
      error: null,
    } as never);
    const { container } = renderPanel();
    expect(container.textContent).toBe("");
    // The flag being off must gate the hook call itself, not just the render —
    // otherwise every unopted-in visit still fires a live suggest request.
    expect(spy).toHaveBeenCalledWith(null, []);
  });

  it("defaults to disabled with no stored preference in a production build", () => {
    // Proves the "a real deploy stays opt-in-only" claim as a test, not
    // just a code comment -- every other test in this file runs under
    // Vitest's DEV=true, so without this, a future refactor that drops the
    // DEV check would silently ship the panel on-by-default in production.
    vi.stubEnv("DEV", false);
    const spy = vi.spyOn(hooks, "useSuggestion").mockReturnValue({
      data: { report_type: "trend", route_code: "R1", reason_text: "should not show", severity: "notable", from_date: "2026-08-15", to_date: "2026-08-15" },
      isPending: false,
      error: null,
    } as never);
    const { container } = renderPanel();
    expect(container.textContent).toBe("");
    expect(spy).toHaveBeenCalledWith(null, []);
  });

  it("defaults to enabled with no stored preference (dev-build default)", () => {
    // No localStorage.setItem here -- import.meta.env.DEV is true under
    // Vitest, matching a real dev build, so this exercises the actual
    // default path rather than an explicit "1".
    vi.spyOn(hooks, "useSuggestion").mockReturnValue({
      data: { report_type: "trend", route_code: "R1", reason_text: "dev default shown", severity: "notable", from_date: "2026-08-15", to_date: "2026-08-15" },
      isPending: false,
      error: null,
    } as never);
    renderPanel();
    expect(screen.getByText("dev default shown")).toBeTruthy();
  });

  it("shows the suggestion and navigates on click when flag is on", () => {
    localStorage.setItem("transit.insightPanelEnabled", "1");
    const spy = vi.spyOn(hooks, "useSuggestion").mockReturnValue({
      data: {
        report_type: "trend",
        route_code: "R1",
        reason_text: "Route R1 is anomalous",
        severity: "notable",
        from_date: "2026-08-15",
        to_date: "2026-08-15",
      },
      isPending: false,
      error: null,
    } as never);
    renderPanel();
    expect(screen.getByText("Route R1 is anomalous")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Open route detail" }));
    // sessionStorage now records this pathway as shown, per the dedup design.
    // Keyed per-agency (agencyId=1 here) -- see seenStorageKey in InsightPanel.tsx.
    expect(sessionStorage.getItem("transit.insightPanelSeen.1")).toContain("trend:R1");
    // The updated "seen" set must flow into the next useSuggestion call too
    // (not just get written to sessionStorage), otherwise the next poll
    // would re-suggest the just-shown route.
    expect(spy).toHaveBeenLastCalledWith(1, ["trend:R1"]);
  });

  it("opens the suggested route's page over the two weeks ending on the suggestion's last day", () => {
    // A one-day window (today's anomaly) would leave every chart there a
    // single point, so the page opens on a fortnight that ends on that day.
    localStorage.setItem("transit.insightPanelEnabled", "1");
    vi.spyOn(hooks, "useSuggestion").mockReturnValue({
      data: { report_type: "dwell_run", route_code: "R1", reason_text: "Route R1 dwell grew", severity: "notable", from_date: "2026-08-20", to_date: "2026-08-20" },
      isPending: false,
      error: null,
    } as never);
    const { router } = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Open route detail" }));
    expect(router.state.location.pathname).toBe("/agencies/1/routes/R1");
    const params = new URLSearchParams(router.state.location.search);
    expect(params.has("report")).toBe(false);
    expect(params.has("routes")).toBe(false);
    expect(params.get("from")).toBe("2026-08-07");
    expect(params.get("to")).toBe("2026-08-20");
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

  it("shows a distinct message when the suggest request errors", () => {
    localStorage.setItem("transit.insightPanelEnabled", "1");
    vi.spyOn(hooks, "useSuggestion").mockReturnValue({
      data: null,
      isPending: false,
      error: new Error("network down"),
    } as never);
    renderPanel();
    expect(screen.getByText("Couldn't load insight right now.")).toBeTruthy();
    expect(screen.queryByText("No notable signal right now.")).toBeNull();
  });

  it("does not crash when sessionStorage holds valid-but-wrong-shaped JSON", () => {
    localStorage.setItem("transit.insightPanelEnabled", "1");
    // Valid JSON, not an array -- JSON.parse succeeds silently, so only a
    // runtime Array.isArray check (not a try/catch) catches this shape.
    sessionStorage.setItem("transit.insightPanelSeen.1", "{}");
    const spy = vi.spyOn(hooks, "useSuggestion").mockReturnValue({
      data: { report_type: "trend", route_code: "R1", reason_text: "ok", severity: "notable", from_date: "2026-08-15", to_date: "2026-08-15" },
      isPending: false,
      error: null,
    } as never);
    renderPanel();
    expect(screen.getByText("ok")).toBeTruthy();
    expect(spy).toHaveBeenCalledWith(1, []);
  });

  it("does not leak one agency's seen pathways into another agency's exclude set", () => {
    localStorage.setItem("transit.insightPanelEnabled", "1");
    sessionStorage.setItem("transit.insightPanelSeen.1", JSON.stringify(["trend:R1"]));
    const spy = vi.spyOn(hooks, "useSuggestion").mockReturnValue({
      data: null,
      isPending: false,
      error: null,
    } as never);
    // A fresh mount for agency 2 (what AnalysisTab's `key={id}` produces on
    // an agency switch) must not inherit agency 1's seen set.
    renderPanel("2");
    expect(spy).toHaveBeenCalledWith(2, []);
  });

  it("keeps the enabled flag fixed at mount instead of re-reading localStorage on every render", () => {
    localStorage.setItem("transit.insightPanelEnabled", "1");
    vi.spyOn(hooks, "useSuggestion").mockReturnValue({
      data: {
        report_type: "trend",
        route_code: "R1",
        reason_text: "Route R1 is anomalous",
        severity: "notable",
        from_date: "2026-08-15",
        to_date: "2026-08-15",
      },
      isPending: false,
      error: null,
    } as never);
    renderPanel();
    // Flip the stored preference off after mount -- a per-render readEnabled()
    // call would hide the panel on the very next re-render triggered below.
    localStorage.setItem("transit.insightPanelEnabled", "0");
    fireEvent.click(screen.getByLabelText("Collapse insight panel"));
    // Still mounted (the `enabled` flag was frozen at mount via
    // useState(readEnabled)), just collapsed -- not vanished, as it would be
    // if `enabled` were re-derived on every render.
    expect(screen.getByLabelText("Expand insight panel")).toBeTruthy();
  });

  it("collapses on click and persists the preference", () => {
    localStorage.setItem("transit.insightPanelEnabled", "1");
    vi.spyOn(hooks, "useSuggestion").mockReturnValue({
      data: {
        report_type: "trend",
        route_code: "R1",
        reason_text: "Route R1 is anomalous",
        severity: "notable",
        from_date: "2026-08-15",
        to_date: "2026-08-15",
      },
      isPending: false,
      error: null,
    } as never);
    renderPanel();
    fireEvent.click(screen.getByLabelText("Collapse insight panel"));
    expect(screen.queryByText("Route R1 is anomalous")).toBeNull();
    expect(localStorage.getItem("transit.insightPanelCollapsed")).toBe("1");
  });
});
