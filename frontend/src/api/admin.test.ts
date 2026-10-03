import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { usePatchUser } from "./admin";

const mockApiPatch = vi.fn();
vi.mock("./client", () => ({
  apiPatch: (...args: unknown[]) => mockApiPatch(...args),
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
