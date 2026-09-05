import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { CopilotPanel } from "./CopilotPanel";
import * as client from "../api/client";

function renderPanel(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <CopilotPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Same panel, but with in-app navigation buttons so a single mounted
 * instance (matching how App.tsx mounts it once outside <Outlet />) can move
 * between routes without remounting — needed to exercise stale-state cleanup
 * across tab changes. */
function renderPanelWithNav(initialPath: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Nav() {
    const navigate = useNavigate();
    return (
      <>
        <button onClick={() => navigate("/agencies/1/overview")}>go-overview</button>
        <button onClick={() => navigate("/agencies/1/map")}>go-map</button>
        <CopilotPanel />
      </>
    );
  }
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Nav />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("CopilotPanel", () => {
  it("shows a step-back note on the Ask tab instead of calling the insight endpoint", () => {
    const spy = vi.spyOn(client, "apiPost");
    renderPanel("/agencies/1/ask");
    // Bilingual match — jsdom's detected language isn't pinned here (unlike
    // renderWithProviders, which forces "en"), so this must tolerate either
    // resource bundle resolving, matching the existing ErrorBanner.test.tsx
    // convention for un-pinned-locale assertions.
    expect(screen.getByText(/こちらで会話が続いています|already in the full conversation/i)).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("renders the fetched insight text on the Overview tab", async () => {
    // The panel only calls the insight endpoint once it has a view_payload,
    // which here comes from the real useOverviewSummary hook — so its
    // underlying apiGet must resolve too, not just apiPost.
    vi.spyOn(client, "apiGet").mockResolvedValue({ headline: { avg_min: 6.4, samples: 812 } });
    vi.spyOn(client, "apiPost").mockResolvedValue({
      text: "Route 12 is delayed.",
      cite: "Overview · 1 sample",
      low_confidence: false,
    });
    renderPanel("/agencies/1/overview");
    await waitFor(() => expect(screen.getByText("Route 12 is delayed.")).toBeTruthy(), { timeout: 2000 });
  });

  it("does not render anything on routes other than Overview/Ask", () => {
    const { container } = renderPanel("/agencies/1/map");
    expect(container.querySelector(".copilot-panel")).toBeNull();
  });

  it("shows a calmer message instead of a generic error when the anon Copilot quota is exceeded", async () => {
    vi.spyOn(client, "apiGet").mockResolvedValue({ headline: { avg_min: 6.4, samples: 812 } });
    vi.spyOn(client, "apiPost").mockRejectedValue(
      new client.ApiError(429, JSON.stringify({ code: "copilot_anon_quota_exceeded" })),
    );
    renderPanel("/agencies/1/overview");
    await waitFor(
      () =>
        expect(
          screen.getByText(/free Copilot insight limit|無料Copilotインサイトの上限/i),
        ).toBeTruthy(),
      { timeout: 2000 },
    );
    expect(screen.queryByText("Couldn't generate an insight right now.")).toBeNull();
  });

  it("clears a stale error instead of leaking it onto an unrelated tab", async () => {
    vi.spyOn(client, "apiGet").mockResolvedValue({ headline: { avg_min: 6.4, samples: 812 } });
    vi.spyOn(client, "apiPost").mockRejectedValue(new Error("boom"));
    const { container } = renderPanelWithNav("/agencies/1/overview");

    await waitFor(
      () => expect(screen.getByText(/couldn't generate an insight|インサイトを生成できません/i)).toBeTruthy(),
      { timeout: 2000 },
    );

    fireEvent.click(screen.getByText("go-map"));

    // The panel doesn't render at all on the Map tab, so the earlier
    // Overview-tab error must not still be showing anywhere.
    expect(container.querySelector(".copilot-panel")).toBeNull();
    expect(screen.queryByText(/couldn't generate an insight|インサイトを生成できません/i)).toBeNull();
  });
});
