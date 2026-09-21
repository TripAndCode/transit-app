import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import * as useRouteNamesModule from "../api/useRouteNames";
import { AskTab } from "./AskTab";

function mockCommonHooks() {
  vi.spyOn(hooks, "useConversations").mockReturnValue({ data: [], isLoading: false } as never);
  vi.spyOn(hooks, "useUpdateConversation").mockReturnValue({ mutateAsync: vi.fn(), mutate: vi.fn(), isPending: false } as never);
  vi.spyOn(hooks, "useDeleteConversation").mockReturnValue({ mutate: vi.fn(), isPending: false } as never);
  vi.spyOn(hooks, "useCreateConversation").mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as never);
  vi.spyOn(hooks, "useAppendMessage").mockReturnValue({ mutate: vi.fn(), isPending: false } as never);
  vi.spyOn(hooks, "useMigrateAnon").mockReturnValue({ mutate: vi.fn(), isPending: false } as never);
  vi.spyOn(hooks, "useFollowup").mockReturnValue({ mutate: vi.fn(), reset: vi.fn(), isPending: false, isError: false, error: null } as never);
  vi.spyOn(hooks, "useFollowupEnabled").mockReturnValue({ data: { enabled: false, max_question_chars: 300 } } as never);
  vi.spyOn(useRouteNamesModule, "useRouteNames").mockReturnValue({
    data: new Map(),
    isLoading: false,
    format: (code: string | null | undefined) => code ?? "—",
  });
}

function renderAsk(initialPath = "/agencies/1/ask") {
  renderWithProviders(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/agencies/:agencyId/ask" element={<AskTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AskTab", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the Ask heading and the landing prompt when there is no active investigation", () => {
    mockCommonHooks();
    vi.spyOn(hooks, "useIsAuthenticated").mockReturnValue(false);
    vi.spyOn(hooks, "useIsLlmApproved").mockReturnValue(false);
    vi.spyOn(hooks, "useConversation").mockReturnValue({ data: null, isPending: false, isError: false } as never);
    renderAsk();
    expect(screen.getByText("Ask")).toBeInTheDocument();
    expect(screen.getByText("Route Intelligence")).toBeInTheDocument();
  });

  it("shows the unavailable message when an active investigation fails to load", () => {
    mockCommonHooks();
    vi.spyOn(hooks, "useIsAuthenticated").mockReturnValue(false);
    vi.spyOn(hooks, "useIsLlmApproved").mockReturnValue(false);
    vi.spyOn(hooks, "useConversation").mockReturnValue({ data: undefined, isPending: false, isError: true, refetch: vi.fn() } as never);
    renderAsk("/agencies/1/ask?conversation=abc-123");
    expect(
      screen.getByText(
        "This investigation could not be opened. Check your connection and account. Guest investigations are available only in the browser where they were saved.",
      ),
    ).toBeInTheDocument();
  });
});
