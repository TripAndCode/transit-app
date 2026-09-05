import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CopilotPanel } from "./CopilotPanel";
import * as client from "../api/client";
import { DEBOUNCE_MS } from "../api/copilot";

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

/** Same panel, but with in-app navigation buttons switching between two
 * different agencies' Overview tabs (not just tabs within one agency, unlike
 * `renderPanelWithNav`) — needed to exercise stale follow-up state cleanup
 * across an agency switch. */
function renderPanelWithAgencySwitch(initialPath: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Nav() {
    const navigate = useNavigate();
    return (
      <>
        <button onClick={() => navigate("/agencies/1/overview")}>go-agency-1</button>
        <button onClick={() => navigate("/agencies/2/overview")}>go-agency-2</button>
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


/** The panel makes two GETs: the `/copilot/enabled` flag check and
 * `useOverviewSummary`. A blanket `mockResolvedValue` would answer the flag
 * check with an overview payload, so route by path instead. */
function mockApiGet(opts: { enabled?: boolean } = {}) {
  return vi.spyOn(client, "apiGet").mockImplementation((path: string) =>
    path.includes("/copilot/enabled")
      ? Promise.resolve({ enabled: opts.enabled ?? true })
      : Promise.resolve({ headline: { avg_min: 6.4, samples: 812 } }),
  ) as unknown as ReturnType<typeof vi.spyOn>;
}

describe("CopilotPanel", () => {
  it("shows a step-back note on the Ask tab instead of calling the insight endpoint", async () => {
    mockApiGet();
    const spy = vi.spyOn(client, "apiPost");
    renderPanel("/agencies/1/ask");
    await waitFor(() => expect(screen.getByRole("complementary")).toBeTruthy());
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
    mockApiGet();
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
    mockApiGet();
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
    mockApiGet();
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
    mockApiGet();
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
    mockApiGet();
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

  it("submits a follow-up question to /ask with panel_ctx", async () => {
    mockApiGet();
    // The first apiPost call is the on-mount proactive-insight fetch (insight
    // shape), the second is the user-submitted follow-up (AskResponse shape)
    // — mocked per-call so the insight render is exercised with its real
    // shape instead of silently rendering undefined fields.
    const spy = vi
      .spyOn(client, "apiPost")
      .mockResolvedValueOnce({
        text: "Route 12 is delayed.",
        cite: "Overview · 1 sample",
        low_confidence: false,
      })
      .mockResolvedValueOnce({
        answer: "It's on time.",
        tool_call: null,
        result: null,
        ctx: {},
      });
    renderPanel("/agencies/1/overview");
    await screen.findByText("Route 12 is delayed.");
    const input = await screen.findByPlaceholderText(/ask a follow-up|続けて質問/i);
    await userEvent.type(input, "how is route 12 doing{enter}");
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(
        "/api/1/ask",
        expect.objectContaining({ question: "how is route 12 doing", panel_ctx: { tab: "overview" } }),
      ),
    );
    expect(await screen.findByText("It's on time.")).toBeTruthy();
  });

  it("clears a stale follow-up answer when switching agencies", async () => {
    mockApiGet();
    vi.spyOn(client, "apiPost").mockResolvedValue({
      text: "Route 12 is delayed.",
      cite: "Overview · 1 sample",
      low_confidence: false,
    });
    renderPanelWithAgencySwitch("/agencies/1/overview");
    await screen.findByText("Route 12 is delayed.");

    vi.spyOn(client, "apiPost").mockResolvedValueOnce({
      answer: "Agency 1 answer.",
      tool_call: null,
      result: null,
      ctx: {},
    });
    const input = await screen.findByPlaceholderText(/ask a follow-up|続けて質問/i);
    await userEvent.type(input, "how is route 12 doing{enter}");
    expect(await screen.findByText("Agency 1 answer.")).toBeTruthy();

    fireEvent.click(screen.getByText("go-agency-2"));

    // Switching agencies must remount the follow-up form, discarding the
    // stale question/answer from the previous agency instead of leaking it
    // under the new agency's panel.
    expect(screen.queryByText("Agency 1 answer.")).toBeNull();
    const newInput = await screen.findByPlaceholderText(/ask a follow-up|続けて質問/i);
    expect((newInput as HTMLInputElement).value).toBe("");
  });

  it("renders the panel when enabled and nothing at all when disabled", async () => {
    mockApiGet({ enabled: true });
    vi.spyOn(client, "apiPost").mockResolvedValue({
      text: "insight",
      cite: "c",
      low_confidence: false,
    } as never);
    const on = renderPanel("/agencies/1/overview");
    await waitFor(() => expect(on.container.querySelector(".copilot-panel")).not.toBeNull());
    on.unmount();

    vi.restoreAllMocks();
    mockApiGet({ enabled: false });
    const postSpy = vi.spyOn(client, "apiPost");
    const off = renderPanel("/agencies/1/overview");
    // Give the flag query the same number of ticks the enabled case needed.
    await waitFor(() => expect(client.apiGet).toHaveBeenCalled());
    expect(off.container.querySelector(".copilot-panel")).toBeNull();
    expect(postSpy).not.toHaveBeenCalled();
  });

  it("stays off and makes no insight request when the flag check fails", async () => {
    const getSpy = vi.spyOn(client, "apiGet").mockRejectedValue(new Error("flag check down"));
    const postSpy = vi.spyOn(client, "apiPost");
    const { container } = renderPanel("/agencies/1/overview");
    await waitFor(() => expect(getSpy).toHaveBeenCalled());
    expect(container.querySelector(".copilot-panel")).toBeNull();
    expect(postSpy).not.toHaveBeenCalled();
  });

  it("does not re-bill the insight when Overview is left and re-entered", async () => {
    mockApiGet();
    const postSpy = vi.spyOn(client, "apiPost").mockResolvedValue({
      text: "Route 12 is delayed.",
      cite: "Overview · 1 sample",
      low_confidence: false,
    } as never);
    renderPanelWithNav("/agencies/1/overview");
    await waitFor(() => expect(screen.getByText("Route 12 is delayed.")).toBeTruthy(), {
      timeout: 3000,
    });
    expect(postSpy).toHaveBeenCalledTimes(1);

    // Off Overview the query key goes null; coming back re-subscribes to the
    // *same* key. Without a staleTime that re-subscription refetches, spending
    // another LLM call and quota unit for a view state that has not changed.
    fireEvent.click(screen.getByText("go-map"));
    await waitFor(() => expect(screen.queryByText("Route 12 is delayed.")).toBeNull());
    // Returning before the key debounce elapses leaves the key untouched and
    // the scenario unexercised, so wait the window out rather than racing it.
    await new Promise((resolve) => setTimeout(resolve, DEBOUNCE_MS + 300));
    fireEvent.click(screen.getByText("go-overview"));
    await waitFor(() => expect(screen.getByText("Route 12 is delayed.")).toBeTruthy(), {
      timeout: 3000,
    });
    expect(postSpy).toHaveBeenCalledTimes(1);
  });
});
