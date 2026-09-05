import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CopilotPanel } from "./CopilotPanel";
import * as client from "../api/client";

// Matches the rest of the suite's convention (e.g. GuestPrompt.test.tsx,
// ReportTable.test.tsx): without this, vi.spyOn(client, ...) across tests
// keeps stacking onto the same spy, so later tests' call counts/histories
// leak earlier tests' calls.
afterEach(() => vi.restoreAllMocks());

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

/** Mirrors the production `QueryClient` in main.tsx (`retry: 1`), unlike
 * `renderPanel`'s test-only `retry: false`. Used to prove the insight query
 * itself opts out of retries (via its own `retry: false`) rather than
 * merely inheriting a test default that would mask a regression. */
function renderPanelWithProductionRetryDefault(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: 1 } } });
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

  it("shows the calm quota-exceeded banner instead of the generic error message", async () => {
    vi.spyOn(client, "apiGet").mockResolvedValue({ headline: { avg_min: 6.4, samples: 812 } });
    vi.spyOn(client, "apiPost").mockRejectedValue(
      new client.ApiError(429, JSON.stringify({ detail: "limit reached", code: "copilot_anon_quota_exceeded" })),
    );
    renderPanel("/agencies/1/overview");
    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy(), { timeout: 2000 });
    expect(
      screen.queryByText(/couldn't generate an insight|インサイトを生成できません/i),
    ).toBeNull();
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

  it("never retries a failed insight POST, even under the production QueryClient's retry:1 default", async () => {
    // A retry here would silently burn a second anonymous-quota unit for
    // what the user experiences as one request (the endpoint consumes quota
    // per attempt with no refund on failure) — so this must hold regardless
    // of the ambient QueryClient default, not just under the test suite's
    // own retry:false QueryClients.
    vi.spyOn(client, "apiGet").mockResolvedValue({ headline: { avg_min: 6.4, samples: 812 } });
    const postSpy = vi.spyOn(client, "apiPost").mockRejectedValue(new Error("boom"));
    renderPanelWithProductionRetryDefault("/agencies/1/overview");

    await waitFor(
      () => expect(screen.getByText(/couldn't generate an insight|インサイトを生成できません/i)).toBeTruthy(),
      { timeout: 2000 },
    );
    // Give a would-be retry a chance to fire before asserting it didn't.
    await new Promise((r) => setTimeout(r, 50));
    expect(postSpy).toHaveBeenCalledTimes(1);
  });

  it("forwards an AbortSignal to apiPost so a superseded in-flight POST can be cancelled", async () => {
    vi.spyOn(client, "apiGet").mockResolvedValue({ headline: { avg_min: 6.4, samples: 812 } });
    const postSpy = vi.spyOn(client, "apiPost").mockResolvedValue({
      text: "Route 12 is delayed.",
      cite: "Overview · 1 sample",
      low_confidence: false,
    });
    renderPanel("/agencies/1/overview");
    await waitFor(() => expect(postSpy).toHaveBeenCalled(), { timeout: 2000 });

    const [, , opts] = postSpy.mock.calls[0];
    expect((opts as { signal?: AbortSignal } | undefined)?.signal).toBeInstanceOf(AbortSignal);
  });
});
