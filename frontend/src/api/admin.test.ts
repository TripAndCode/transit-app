import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useAckBoardAlert, usePatchUser, type AdminBoard } from "./admin";

const mockApiPatch = vi.fn();
const mockApiPost = vi.fn();
vi.mock("./client", () => ({
  apiPatch: (...args: unknown[]) => mockApiPatch(...args),
  apiPost: (...args: unknown[]) => mockApiPost(...args),
}));

describe("usePatchUser", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockApiPatch.mockReset();
  });

  it("PATCHes the user and invalidates the admin user list and detail queries on success", async () => {
    mockApiPatch.mockResolvedValue({ user_id: 5, role: "admin" });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => usePatchUser(), {
      wrapper: ({ children }) => createElement(QueryClientProvider, { client: queryClient }, children),
    });

    let outcome: Awaited<ReturnType<typeof result.current.mutateAsync>> | undefined;
    await act(async () => {
      outcome = await result.current.mutateAsync({ uid: 5, body: { role: "admin" } });
    });

    expect(mockApiPatch).toHaveBeenCalledWith("/api/admin/users/5", { role: "admin" });
    expect(outcome).toEqual({ user_id: 5, role: "admin" });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["adminUsers"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["adminUser"] });
  });

  it("does not invalidate anything when the PATCH fails", async () => {
    mockApiPatch.mockRejectedValue(new Error("boom"));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => usePatchUser(), {
      wrapper: ({ children }) => createElement(QueryClientProvider, { client: queryClient }, children),
    });

    await act(async () => {
      await expect(result.current.mutateAsync({ uid: 5, body: { role: "admin" } })).rejects.toThrow("boom");
    });

    expect(invalidateSpy).not.toHaveBeenCalled();
  });
});

describe("useAckBoardAlert", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    mockApiPost.mockReset();
  });

  const alert = (key: string, acked = false) => ({
    level: "warn" as const,
    code: "agency_stale",
    params: {},
    text: key,
    href: null,
    key,
    acked,
  });

  function setup() {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const board: AdminBoard = { collectors: [], freshness: [], migrations: null, runs: [], alerts: [alert("a1"), alert("b2")] };
    queryClient.setQueryData(["adminBoard"], board);
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useAckBoardAlert(), {
      wrapper: ({ children }) => createElement(QueryClientProvider, { client: queryClient }, children),
    });
    return { queryClient, invalidateSpy, result };
  }

  it("POSTs the acknowledgement and marks that alert acknowledged in the cached board at once", async () => {
    mockApiPost.mockResolvedValue(undefined);
    const { queryClient, invalidateSpy, result } = setup();
    await act(async () => {
      await result.current.mutateAsync("a1");
    });
    expect(mockApiPost).toHaveBeenCalledWith("/api/admin/board/alerts/a1/ack", {});
    const alerts = queryClient.getQueryData<AdminBoard>(["adminBoard"])!.alerts;
    expect(alerts.map((a) => [a.key, a.acked])).toEqual([["a1", true], ["b2", false]]);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["adminBoard"] });
  });

  it("leaves the cached board alone when the acknowledgement fails", async () => {
    mockApiPost.mockRejectedValue(new Error("403"));
    const { queryClient, result } = setup();
    await act(async () => {
      await expect(result.current.mutateAsync("a1")).rejects.toThrow("403");
    });
    expect(queryClient.getQueryData<AdminBoard>(["adminBoard"])!.alerts.every((a) => !a.acked)).toBe(true);
  });
});
