import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CopilotPanel } from "./CopilotPanel";
import * as client from "../api/client";
import { DEBOUNCE_MS } from "../api/copilot";

// Matches the rest of the suite's convention (e.g. GuestPrompt.test.tsx,
// ReportTable.test.tsx): without this, vi.spyOn(client, ...) across tests
// keeps stacking onto the same spy, so later tests' call counts/histories
// leak earlier tests' calls.
afterEach(() => vi.restoreAllMocks());

// @testing-library/dom's `waitFor`/`findBy*` only drive a fake clock forward
// themselves (instead of polling real wall-clock time, which would hang
// forever once timers are faked) when they detect a Jest-shaped global fake
// timer -- vitest's `vi` doesn't match that check on its own. Shimming just
// the one method it calls is enough to make every waitFor/findBy below
// resolve as soon as the panel's real DEBOUNCE_MS timer (and any promise
// chain past it) settles, with no per-call advance needed. A handful of
// spots below with no waitFor/findBy after them still advance the clock
// explicitly, since nothing else would.
declare global {
  var jest: { advanceTimersByTime: (ms: number) => unknown } | undefined;
}
beforeEach(() => {
  globalThis.jest = { advanceTimersByTime: vi.advanceTimersByTime };
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  delete globalThis.jest;
});

/** Same fake-timer-aware setup `userEvent`'s own docs recommend: without
 * `advanceTimers`, userEvent's internal per-keystroke delay awaits a
 * setTimeout that these fake timers never advance on their own, so typing
 * would hang forever. */
function setupUser() {
  return userEvent.setup({ delay: null, advanceTimers: vi.advanceTimersByTime });
}

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
        <button onClick={() => navigate("/agencies/1/period-overview")}>go-overview</button>
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
        <button onClick={() => navigate("/agencies/1/period-overview")}>go-agency-1</button>
        <button onClick={() => navigate("/agencies/2/period-overview")}>go-agency-2</button>
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
 * check with an overview payload, so route by path instead.
 *
 * The session is stubbed separately (`apiGetOrNull`, which `useSession`
 * uses) because the insight POST now also requires this caller's own
 * `llm_approved`. It defaults to an approved session so each behavioral test
 * exercises the path it is actually about; pass `llmApproved: false` to
 * exercise the gate itself. */
function mockApiGet(opts: { enabled?: boolean; llmApproved?: boolean } = {}) {
  vi.spyOn(client, "apiGetOrNull").mockResolvedValue({
    user_id: 1,
    email: "t@test",
    name: "T",
    avatar_url: null,
    role: "user",
    llm_approved: opts.llmApproved ?? true,
  } as never);
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

  it("never fires the insight POST for a caller an admin hasn't approved", async () => {
    // The insight fires from a pageview, not a user action, and the endpoint
    // 403s an unapproved caller -- and `llm_approved` is false for every new
    // account, so without this gate the default experience is one doomed
    // request per Overview visit.
    mockApiGet({ llmApproved: false });
    const spy = vi.spyOn(client, "apiPost");
    renderPanel("/agencies/1/period-overview");
    await waitFor(() => expect(client.apiGet).toHaveBeenCalled());
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
    renderPanel("/agencies/1/period-overview");
    await waitFor(() => expect(screen.getByText("Route 12 is delayed.")).toBeTruthy());
  });

  it("does not render anything on routes other than Overview/Ask", () => {
    const { container } = renderPanel("/agencies/1/map");
    expect(container.querySelector(".copilot-panel")).toBeNull();
  });

  it("shows the calm admin-approval-required banner instead of the generic error message", async () => {
    mockApiGet();
    vi.spyOn(client, "apiPost").mockRejectedValue(
      new client.ApiError(403, JSON.stringify({ detail: "llm_not_approved" })),
    );
    renderPanel("/agencies/1/period-overview");
    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
    expect(
      screen.queryByText(/couldn't generate an insight|インサイトを生成できません/i),
    ).toBeNull();
  });

  it("clears a stale error instead of leaking it onto an unrelated tab", async () => {
    mockApiGet();
    vi.spyOn(client, "apiPost").mockRejectedValue(new Error("boom"));
    const { container } = renderPanelWithNav("/agencies/1/period-overview");

    await waitFor(() =>
      expect(screen.getByText(/couldn't generate an insight|インサイトを生成できません/i)).toBeTruthy(),
    );

    fireEvent.click(screen.getByText("go-map"));

    // The panel doesn't render at all on the Map tab, so the earlier
    // Overview-tab error must not still be showing anywhere.
    expect(container.querySelector(".copilot-panel")).toBeNull();
    expect(screen.queryByText(/couldn't generate an insight|インサイトを生成できません/i)).toBeNull();
  });

  it("never retries a failed insight POST, even under the production QueryClient's retry:1 default", async () => {
    // A retry here would silently pay for a second provider call for what
    // the user experiences as one request — so this must hold regardless of
    // the ambient QueryClient default, not just under the test suite's own
    // retry:false QueryClients.
    mockApiGet();
    const postSpy = vi.spyOn(client, "apiPost").mockRejectedValue(new Error("boom"));
    renderPanelWithProductionRetryDefault("/agencies/1/period-overview");

    await waitFor(() =>
      expect(screen.getByText(/couldn't generate an insight|インサイトを生成できません/i)).toBeTruthy(),
    );
    // Give a would-be retry a chance to fire before asserting it didn't.
    await vi.advanceTimersByTimeAsync(50);
    expect(postSpy).toHaveBeenCalledTimes(1);
  });

  it("forwards an AbortSignal to apiPost so a superseded in-flight POST can be cancelled", async () => {
    mockApiGet();
    const postSpy = vi.spyOn(client, "apiPost").mockResolvedValue({
      text: "Route 12 is delayed.",
      cite: "Overview · 1 sample",
      low_confidence: false,
    });
    renderPanel("/agencies/1/period-overview");
    await waitFor(() => expect(postSpy).toHaveBeenCalled());

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
    renderPanel("/agencies/1/period-overview");
    await screen.findByText("Route 12 is delayed.");
    const input = await screen.findByPlaceholderText(/ask a follow-up|続けて質問/i);
    await setupUser().type(input, "how is route 12 doing{enter}");
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
    renderPanelWithAgencySwitch("/agencies/1/period-overview");
    await screen.findByText("Route 12 is delayed.");

    vi.spyOn(client, "apiPost").mockResolvedValueOnce({
      answer: "Agency 1 answer.",
      tool_call: null,
      result: null,
      ctx: {},
    });
    const input = await screen.findByPlaceholderText(/ask a follow-up|続けて質問/i);
    await setupUser().type(input, "how is route 12 doing{enter}");
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
    const on = renderPanel("/agencies/1/period-overview");
    await waitFor(() => expect(on.container.querySelector(".copilot-panel")).not.toBeNull());
    on.unmount();

    vi.restoreAllMocks();
    mockApiGet({ enabled: false });
    const postSpy = vi.spyOn(client, "apiPost");
    const off = renderPanel("/agencies/1/period-overview");
    await waitFor(() => expect(client.apiGet).toHaveBeenCalled());
    expect(off.container.querySelector(".copilot-panel")).toBeNull();
    // Advance past the key debounce before asserting no POST. Rendering
    // nothing and issuing nothing are two separate gates — the early return
    // covers the first, `tab` covers the second — and the POST only fires
    // DEBOUNCE_MS later, so asserting immediately would pass with the `tab`
    // gate removed.
    await act(() => vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 300));
    expect(postSpy).not.toHaveBeenCalled();
  });

  it("stays off and makes no insight request when the flag check fails", async () => {
    const getSpy = vi.spyOn(client, "apiGet").mockRejectedValue(new Error("flag check down"));
    const postSpy = vi.spyOn(client, "apiPost");
    const { container } = renderPanel("/agencies/1/period-overview");
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
    renderPanelWithNav("/agencies/1/period-overview");
    await waitFor(() => expect(screen.getByText("Route 12 is delayed.")).toBeTruthy());
    expect(postSpy).toHaveBeenCalledTimes(1);

    // Off Overview the query key goes null; coming back re-subscribes to the
    // *same* key. Without a staleTime that re-subscription refetches, spending
    // another LLM call for a view state that has not changed.
    fireEvent.click(screen.getByText("go-map"));
    await waitFor(() => expect(screen.queryByText("Route 12 is delayed.")).toBeNull());
    // Clicking back before this key-debounce timer actually fires would let
    // its cleanup cancel it, leaving `debounced.key` at its old non-null
    // value and the null-key round trip below unexercised -- so this must be
    // an explicit advance, not left for a later waitFor to drive.
    await act(() => vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 300));
    fireEvent.click(screen.getByText("go-overview"));
    await waitFor(() => expect(screen.getByText("Route 12 is delayed.")).toBeTruthy());
    expect(postSpy).toHaveBeenCalledTimes(1);
  });
});
