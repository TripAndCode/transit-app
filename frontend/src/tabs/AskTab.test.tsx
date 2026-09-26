import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { AskTab } from "./AskTab";
import * as hooks from "../api/hooks";
import { conversationsAnon } from "../api/conversationsAnon";
import type { ConvMessage } from "../api/types";

function mutationStub(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    mutate: vi.fn(),
    mutateAsync: vi.fn().mockResolvedValue(undefined),
    isPending: false,
    isError: false,
    error: null,
    reset: vi.fn(),
    ...overrides,
  } as never;
}

function mockAllHooks() {
  vi.spyOn(hooks, "useIsAuthenticated").mockReturnValue(false);
  vi.spyOn(hooks, "useIsLlmApproved").mockReturnValue(false);
  vi.spyOn(hooks, "useMigrateAnon").mockReturnValue(mutationStub());
  vi.spyOn(hooks, "useConversation").mockReturnValue({
    data: undefined,
    isPending: false,
    isError: false,
    refetch: vi.fn(),
  } as never);
  vi.spyOn(hooks, "useCreateConversation").mockReturnValue(mutationStub());
  vi.spyOn(hooks, "useAppendMessage").mockReturnValue(mutationStub());
  vi.spyOn(hooks, "useUpdateConversation").mockReturnValue(mutationStub());
  vi.spyOn(hooks, "useFollowup").mockReturnValue(mutationStub());
  vi.spyOn(hooks, "useFollowupEnabled").mockReturnValue({ data: { enabled: false, max_question_chars: 500 } } as never);
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
  vi.spyOn(hooks, "useConversations").mockReturnValue({ data: [], isLoading: false } as never);
  vi.spyOn(hooks, "useDeleteConversation").mockReturnValue(mutationStub());
  vi.spyOn(conversationsAnon, "exportAll").mockReturnValue([]);
}

function renderAskTab(initialPath = "/agencies/5/ask") {
  return renderWithProviders(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/agencies/:agencyId/ask" element={<AskTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AskTab layout", () => {
  beforeEach(() => {
    mockAllHooks();
    // jsdom doesn't implement scrollTo; AskTab calls it to reset scroll
    // position on a message-list change.
    Element.prototype.scrollTo = vi.fn();
  });

  it("renders the question composer above the landing cards when there are no messages", () => {
    const { container } = renderAskTab();
    const dock = container.querySelector(".ask-tool-menu");
    const landing = screen.getByText(new RegExp("^Ask about a delay$"));
    expect(dock).toBeInTheDocument();
    // DOCUMENT_POSITION_PRECEDING (2) means the dock comes before the
    // landing content in the DOM -- "moved to the top" for a fresh visit.
    expect(dock!.compareDocumentPosition(landing) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("keeps the composer pinned at the bottom, after the conversation, once there are messages", () => {
    const messages: ConvMessage[] = [
      {
        message_id: 1,
        conversation_id: "t1",
        role: "user",
        chip_id: null,
        tool: null,
        args: null,
        signature_hash: null,
        result: null,
        rendered_summary: "Hi",
        created_at: "2026-09-01T00:00:00Z",
      },
    ];
    vi.spyOn(hooks, "useConversation").mockReturnValue({
      data: {
        conversation: {
          conversation_id: "t1",
          user_id: null,
          agency_id: 5,
          title: "T",
          filter_ctx: {},
          pinned: false,
          created_at: "",
          updated_at: "",
        },
        messages,
      },
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    } as never);

    const { container } = renderAskTab("/agencies/5/ask?conversation=t1");
    const dock = container.querySelector(".ask-tool-menu");
    const conversation = screen.getByText("Hi");
    expect(dock).toBeInTheDocument();
    // Now the dock comes AFTER the conversation area.
    expect(dock!.compareDocumentPosition(conversation) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });
});
