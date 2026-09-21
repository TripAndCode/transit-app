import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "../i18n";
import { useReport, useConversations, useConversation, useAppendMessage } from "./hooks";
import type { RangeCtx } from "./rangeContext";
import type { AnonThread, Conversation, DefinitionMeta, ReportResponse } from "./types";

void i18n.changeLanguage("en");

const mockApiGet = vi.fn();
const mockApiPost = vi.fn();
const mockApiPatch = vi.fn();
const mockApiDelete = vi.fn();
vi.mock("./client", () => ({
  apiGet: (...args: unknown[]) => mockApiGet(...args),
  apiPost: (...args: unknown[]) => mockApiPost(...args),
  apiPatch: (...args: unknown[]) => mockApiPatch(...args),
  apiDelete: (...args: unknown[]) => mockApiDelete(...args),
}));

// `useIsAuthenticated`/`useIsLlmApproved` are private to hooks.ts (not
// exported), so they can't be spied on directly from outside the module —
// their only externally visible dependency is `useSession` from "./auth".
// Mocking that module drives the authed/anon fork the same way mocking
// `useIsAuthenticated` itself would.
let sessionUserId: number | null = null;
vi.mock("./auth", () => ({
  useSession: () => ({ data: sessionUserId != null ? { user_id: sessionUserId, llm_approved: false } : null }),
}));

const conversationsAnonMock = vi.hoisted(() => ({
  list: vi.fn((): AnonThread[] => []),
  get: vi.fn((): AnonThread | undefined => undefined),
  create: vi.fn(),
  update: vi.fn(),
  delete: vi.fn(),
  appendMessage: vi.fn(),
  exportAll: vi.fn((): AnonThread[] => []),
  clearAll: vi.fn(),
}));
vi.mock("./conversationsAnon", () => ({ conversationsAnon: conversationsAnonMock }));

const DEFINITION: DefinitionMeta = {
  preset: null,
  early_tolerance_sec: null,
  late_tolerance_sec: null,
  exclusion_threshold_sec: 7200,
  measurement_point: "all_stops_all_observations",
  dedup_rule: "latest_observation_per_stop_event",
};

function report(): ReportResponse {
  return { report_type: "trend", rendered_at: "x", text: "", rows: [], definition: DEFINITION };
}

function baseCtx(): RangeCtx {
  return { from: "2026-01-01", to: "2026-01-31", dow: "all", time_band: "all", service: "all", routes: [] };
}

function withProviders(queryClient: QueryClient) {
  return { wrapper: ({ children }: { children: React.ReactNode }) => createElement(QueryClientProvider, { client: queryClient }, children) };
}

afterEach(() => {
  vi.restoreAllMocks();
  mockApiGet.mockReset();
  mockApiPost.mockReset();
  mockApiPatch.mockReset();
  mockApiDelete.mockReset();
  sessionUserId = null;
  for (const fn of Object.values(conversationsAnonMock)) fn.mockReset();
  conversationsAnonMock.list.mockReturnValue([]);
  conversationsAnonMock.exportAll.mockReturnValue([]);
});

// ─── ctxKey: exercised indirectly through the queryKey it feeds into every
// range-scoped hook (useReport here). Not exported, so these assertions are
// on its only observable effect: whether react-query treats two contexts as
// the same query or a different one. ────────────────────────────────────────
describe("ctxKey (observed via useReport's cache identity)", () => {
  it("reuses the same cached query for two ctx objects with identical values", async () => {
    mockApiGet.mockResolvedValue(report());
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const c1 = baseCtx();
    const c2 = { ...baseCtx() }; // distinct object, identical values
    const { result } = renderHook(
      () => ({ a: useReport(1, "trend", c1), b: useReport(1, "trend", c2) }),
      withProviders(queryClient),
    );
    await waitFor(() => expect(result.current.a.isSuccess).toBe(true));
    expect(mockApiGet).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["from", { ...baseCtx(), from: "2026-02-01" }],
    ["to", { ...baseCtx(), to: "2026-02-28" }],
    ["dow", { ...baseCtx(), dow: "weekday" as const }],
    ["time_band", { ...baseCtx(), time_band: "morning" as const }],
    ["service", { ...baseCtx(), service: "平日" as const }],
    ["routes", { ...baseCtx(), routes: ["R1"] }],
  ])("treats a ctx differing only in %s as a distinct query", async (_dimension, variant) => {
    mockApiGet.mockResolvedValue(report());
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const base = baseCtx();
    const { result } = renderHook(
      () => ({ a: useReport(1, "trend", base), b: useReport(1, "trend", variant) }),
      withProviders(queryClient),
    );
    await waitFor(() => expect(result.current.a.isSuccess && result.current.b.isSuccess).toBe(true));
    expect(mockApiGet).toHaveBeenCalledTimes(2);
  });
});

// ─── builderSummary: also private to hooks.ts. Exercised through the one
// caller that falls back to it — useAppendMessage's anonymous path, when no
// explicit user_summary is supplied. ─────────────────────────────────────────
describe("builderSummary (observed via useAppendMessage's anon fallback label)", () => {
  it("renders a translated tool label plus up to 4 translated key: value pairs", async () => {
    sessionUserId = null; // anonymous
    mockApiPost.mockResolvedValue({
      answer: "done",
      tool_call: null,
      result: null,
      ctx: { from: "2026-01-01", to: "2026-01-31", dow: "all", time_band: "all" },
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useAppendMessage(1), withProviders(queryClient));

    // Assert on mutateAsync's own resolved value rather than the mutation
    // hook's `data` field: react-query settles that field via a subsequent
    // render commit that isn't guaranteed to have flushed the instant
    // mutateAsync's promise resolves, even inside `act`.
    let outcome: Awaited<ReturnType<typeof result.current.mutateAsync>> | undefined;
    await act(async () => {
      outcome = await result.current.mutateAsync({
        conversationId: "local-1",
        tool: "top_n",
        args: { metric: "avg_delay", n: 5, best_first: true },
      });
    });

    expect(outcome?.user.rendered_summary).toBe(
      "🛠 Ranking (Metric: Average delay, Count: 5, Best first: Yes)",
    );
  });

  it("falls back to the tool name itself for an unrecognized tool with no args", async () => {
    sessionUserId = null;
    mockApiPost.mockResolvedValue({
      answer: "done",
      tool_call: null,
      result: null,
      ctx: { from: "2026-01-01", to: "2026-01-31", dow: "all", time_band: "all" },
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useAppendMessage(1), withProviders(queryClient));

    let outcome: Awaited<ReturnType<typeof result.current.mutateAsync>> | undefined;
    await act(async () => {
      outcome = await result.current.mutateAsync({ conversationId: "local-1", tool: "mystery_tool", args: {} });
    });

    expect(outcome?.user.rendered_summary).toBe("🛠 mystery_tool");
  });
});

// ─── Authed vs. anonymous fork ──────────────────────────────────────────────
describe("useConversations authed/anon fork", () => {
  it("fetches from the server when authenticated", async () => {
    sessionUserId = 42;
    mockApiGet.mockResolvedValue([]);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useConversations(1), withProviders(queryClient));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mockApiGet).toHaveBeenCalledWith("/api/1/conversations", expect.anything());
    expect(conversationsAnonMock.list).not.toHaveBeenCalled();
  });

  it("reads from local storage (converted to the server shape) when anonymous", async () => {
    sessionUserId = null;
    conversationsAnonMock.list.mockReturnValue([
      {
        client_id: "c1",
        agency_id: 1,
        title: "Anon thread",
        filter_ctx: {},
        pinned: false,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
        messages: [],
      },
    ]);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useConversations(1), withProviders(queryClient));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mockApiGet).not.toHaveBeenCalled();
    const data = result.current.data as Conversation[];
    expect(data).toHaveLength(1);
    expect(data[0]).toMatchObject({ conversation_id: "c1", user_id: null, title: "Anon thread" });
  });
});

describe("useConversation authed/anon fork", () => {
  it("fetches conversation + messages from the server when authenticated", async () => {
    sessionUserId = 7;
    mockApiGet.mockImplementation((path: string) =>
      path.endsWith("/messages") ? Promise.resolve([{ message_id: 1 }]) : Promise.resolve({ conversation_id: "s1", title: "Server" }),
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useConversation(1, "s1"), withProviders(queryClient));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.conversation).toMatchObject({ conversation_id: "s1", title: "Server" });
    expect(result.current.data?.messages).toHaveLength(1);
    expect(conversationsAnonMock.get).not.toHaveBeenCalled();
  });

  it("reads the thread from local storage when anonymous", async () => {
    sessionUserId = null;
    conversationsAnonMock.get.mockReturnValue({
      client_id: "c1",
      agency_id: 1,
      title: "Anon thread",
      filter_ctx: {},
      pinned: false,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      messages: [
        {
          message_id: -1,
          conversation_id: "c1",
          role: "user",
          chip_id: null,
          tool: null,
          args: null,
          signature_hash: null,
          result: null,
          rendered_summary: "hi",
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useConversation(1, "c1"), withProviders(queryClient));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mockApiGet).not.toHaveBeenCalled();
    expect(result.current.data?.conversation).toMatchObject({ conversation_id: "c1", user_id: null });
    expect(result.current.data?.messages).toHaveLength(1);
  });
});

describe("useAppendMessage authed/anon fork", () => {
  it("posts to the server conversation-messages endpoint when authenticated", async () => {
    sessionUserId = 9;
    mockApiPost.mockResolvedValue({ user: { message_id: 1 }, assistant: { message_id: 2 } });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useAppendMessage(1), withProviders(queryClient));

    await act(async () => {
      await result.current.mutateAsync({ conversationId: "s1", tool: "top_n", args: {}, user_summary: "Ranking" });
    });

    expect(mockApiPost).toHaveBeenCalledWith(
      "/api/1/conversations/s1/messages",
      { tool: "top_n", args: {}, user_summary: "Ranking" },
    );
    expect(conversationsAnonMock.appendMessage).not.toHaveBeenCalled();
  });

  it("dispatches via /ask and appends both messages to local storage when anonymous", async () => {
    sessionUserId = null;
    mockApiPost.mockResolvedValue({
      answer: "The answer",
      tool_call: null,
      result: null,
      ctx: { from: "2026-01-01", to: "2026-01-31", dow: "all", time_band: "all" },
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useAppendMessage(1), withProviders(queryClient));

    let outcome: Awaited<ReturnType<typeof result.current.mutateAsync>> | undefined;
    await act(async () => {
      outcome = await result.current.mutateAsync({ conversationId: "local-1", tool: "top_n", args: {}, user_summary: "Ranking" });
    });

    expect(mockApiPost).toHaveBeenCalledWith("/api/1/ask", { question: expect.stringContaining("__build__ top_n") });
    expect(conversationsAnonMock.appendMessage).toHaveBeenCalledTimes(2);
    expect(outcome?.assistant.rendered_summary).toBe("The answer");
  });
});
