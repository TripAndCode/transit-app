import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as client from "./client";
import { useTimeline } from "./hooks";

const EMPTY_DAY = { date: "2026-03-04", step_minutes: 60, frames: [] };

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

afterEach(() => vi.restoreAllMocks());

describe("useTimeline", () => {
  it("asks for the whole agency when no routes are scoped", async () => {
    const apiGet = vi.spyOn(client, "apiGet").mockResolvedValue(EMPTY_DAY);
    renderHook(() => useTimeline(1, true), { wrapper: wrapper() });
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(1));
    expect(apiGet.mock.calls[0][0]).toBe("/api/1/delays/timeline");
  });

  it("sends the scoped routes as a comma list, so playback shows the same selection as the map", async () => {
    const apiGet = vi.spyOn(client, "apiGet").mockResolvedValue(EMPTY_DAY);
    renderHook(() => useTimeline(1, true, ["50", "51"]), { wrapper: wrapper() });
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(1));
    const url = new URL(apiGet.mock.calls[0][0] as string, "http://x");
    expect(url.pathname).toBe("/api/1/delays/timeline");
    expect(url.searchParams.get("routes")).toBe("50,51");
  });

  it("refetches when the routes change rather than reusing another selection's frames", async () => {
    const apiGet = vi.spyOn(client, "apiGet").mockResolvedValue(EMPTY_DAY);
    const { rerender } = renderHook(({ routes }) => useTimeline(1, true, routes), {
      wrapper: wrapper(),
      initialProps: { routes: ["50"] },
    });
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(1));
    rerender({ routes: ["51"] });
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(2));
  });
});
