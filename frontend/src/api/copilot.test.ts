import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import i18n from "../i18n";
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCopilotEnabled, useCopilotInsight, DEBOUNCE_MS } from "./copilot";

const mockApiGet = vi.fn();
const mockApiPost = vi.fn();
vi.mock("./client", () => ({
  apiGet: (...args: unknown[]) => mockApiGet(...args),
  apiPost: (...args: unknown[]) => mockApiPost(...args),
}));

function setup<T>(fn: () => T) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return renderHook(fn, { wrapper: ({ children }) => createElement(QueryClientProvider, { client: queryClient }, children) });
}

describe("useCopilotEnabled", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockApiGet.mockReset();
  });

  it("fetches the agency's copilot kill switch", async () => {
    mockApiGet.mockResolvedValue({ enabled: true });
    const { result } = setup(() => useCopilotEnabled(1));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mockApiGet).toHaveBeenCalledWith("/api/1/copilot/enabled", expect.anything());
    expect(result.current.data?.enabled).toBe(true);
  });

  it("does not fetch when there is no agency yet", () => {
    setup(() => useCopilotEnabled(null));
    expect(mockApiGet).not.toHaveBeenCalled();
  });
});

describe("useCopilotInsight", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    mockApiPost.mockReset();
  });

  it("debounces the very first request the same as a later key change, instead of firing immediately on mount", async () => {
    mockApiPost.mockResolvedValue({ text: "x", cite: "c", low_confidence: false });
    setup(() => useCopilotInsight(1, "overview", { some: "payload" }));

    // Flush microtasks without advancing past DEBOUNCE_MS -- a regression
    // that skips the debounce on the initial key fires the POST here.
    await vi.advanceTimersByTimeAsync(0);
    expect(mockApiPost).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 50);
    await vi.waitFor(() => expect(mockApiPost).toHaveBeenCalledTimes(1));
    expect(mockApiPost).toHaveBeenCalledWith(
      "/api/1/copilot/insight",
      { tab: "overview", view_payload: { some: "payload" } },
      expect.anything(),
    );
  });

  it("debounces a view-payload change instead of refetching on every keystroke", async () => {
    mockApiPost.mockResolvedValue({ text: "Insight text", cite: "route 1", low_confidence: false });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const { result, rerender } = renderHook(
      ({ payload }: { payload: unknown }) => useCopilotInsight(1, "overview", payload),
      {
        initialProps: { payload: { headline: "x" } },
        wrapper: ({ children }) => createElement(QueryClientProvider, { client: queryClient }, children),
      },
    );
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 50);
    await vi.waitFor(() => expect(result.current.insight).not.toBeNull());
    mockApiPost.mockClear();

    // A rapid second change should not fire a second request until the
    // debounce window has elapsed.
    rerender({ payload: { headline: "y" } });
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS / 2);
    expect(mockApiPost).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS / 2 + 10);
    await vi.waitFor(() => expect(mockApiPost).toHaveBeenCalledTimes(1));
    expect(mockApiPost).toHaveBeenCalledWith(
      "/api/1/copilot/insight",
      { tab: "overview", view_payload: { headline: "y" } },
      expect.anything(),
    );
  });

  it("asks again after a language switch, rather than keeping the insight in the old language", async () => {
    mockApiPost.mockResolvedValue({ text: "x", cite: "c", low_confidence: false });
    await i18n.changeLanguage("en");
    const { result } = setup(() => useCopilotInsight(1, "overview", { some: "payload" }));
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 50);
    await vi.waitFor(() => expect(result.current.insight).not.toBeNull());
    expect(mockApiPost).toHaveBeenCalledTimes(1);

    await act(async () => {
      await i18n.changeLanguage("ja");
    });
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS + 50);
    await vi.waitFor(() => expect(mockApiPost).toHaveBeenCalledTimes(2));
    await i18n.changeLanguage("en");
  });

  it("never fetches when there is no agency, tab, or view payload yet", async () => {
    setup(() => useCopilotInsight(null, null, null));
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    expect(mockApiPost).not.toHaveBeenCalled();
  });
});
