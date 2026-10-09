import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useConversation, useConversations } from "./hooks";

const mockApiGet = vi.fn();
vi.mock("./client", () => ({
  apiGet: (...args: unknown[]) => mockApiGet(...args),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
  apiPost: vi.fn(),
}));

const mockUseSession = vi.fn();
vi.mock("./auth", () => ({
  useSession: () => mockUseSession(),
}));

function setup<T>(hook: () => T) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return renderHook(hook, {
    wrapper: ({ children }) => createElement(QueryClientProvider, { client: queryClient }, children),
  });
}

describe("conversation family agencyId guard", () => {
  afterEach(() => {
    mockApiGet.mockReset();
    mockUseSession.mockReset();
  });

  it("useConversations does not fetch for an unresolved agencyId of 0", () => {
    mockUseSession.mockReturnValue({ data: { user_id: 1 } });
    setup(() => useConversations(0));
    expect(mockApiGet).not.toHaveBeenCalled();
  });

  it("useConversation does not fetch for an unresolved agencyId of 0, even with a conversationId", () => {
    mockUseSession.mockReturnValue({ data: { user_id: 1 } });
    setup(() => useConversation(0, "conv-1"));
    expect(mockApiGet).not.toHaveBeenCalled();
  });

  it("useConversations still fetches for a real agencyId", () => {
    mockUseSession.mockReturnValue({ data: { user_id: 1 } });
    mockApiGet.mockResolvedValue([]);
    setup(() => useConversations(1));
    expect(mockApiGet).toHaveBeenCalledWith("/api/1/conversations", expect.anything());
  });
});
