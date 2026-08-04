import { describe, it, expect, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useLogout } from "./auth";
import * as client from "./client";

vi.mock("./client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./client")>();
  return { ...actual, apiPost: vi.fn().mockResolvedValue(undefined) };
});

describe("useLogout", () => {
  it("clears the entire query cache, not just the session key", async () => {
    // A prior version only invalidated ["me"], leaving every other
    // session-scoped query (admin users/agencies/ops, conversations, ...)
    // showing stale cached data in any tab/view that doesn't happen to do a
    // full page reload after logout - the one caller that does reload
    // (AccountPage) papered over this, but useLogout itself must be safe
    // for any future caller that doesn't.
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(["me"], { user_id: 1, email: "a@b.com" });
    qc.setQueryData(["adminUsers"], [{ id: 1, email: "a@b.com" }]);
    qc.setQueryData(["conversations", 5], [{ id: "t1" }]);

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useLogout(), { wrapper });

    result.current.mutate();

    await waitFor(() => expect(client.apiPost).toHaveBeenCalledWith("/api/auth/logout", {}));
    await waitFor(() => expect(qc.getQueryData(["me"])).toBeUndefined());
    expect(qc.getQueryData(["adminUsers"])).toBeUndefined();
    expect(qc.getQueryData(["conversations", 5])).toBeUndefined();
  });
});
