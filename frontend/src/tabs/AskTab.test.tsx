import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { AskTab } from "./AskTab";
import * as hooks from "../api/hooks";
import * as config from "../api/config";
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

  it("titles the screen with one level-1 heading, above the investigations bar", () => {
    const { container } = renderAskTab();
    const heading = screen.getByRole("heading", { level: 1, name: "Notebook" });
    const bar = container.querySelector(".ask-workspace-bar");
    expect(bar).not.toBeNull();
    expect(heading.compareDocumentPosition(bar!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The bar holds only the investigations menu; the screen name is the h1's.
    expect(bar!.textContent).not.toMatch(/^Notebook/);
  });

  it("renders the question composer above the landing cards when there are no messages", () => {
    const { container } = renderAskTab();
    const dock = container.querySelector(".ask-tool-menu");
    const landing = screen.getByText(new RegExp("^Pick a ready-made analysis$"));
    expect(dock).toBeInTheDocument();
    // DOCUMENT_POSITION_FOLLOWING (4) on dock.compareDocumentPosition(landing)
    // means the landing follows the dock in the DOM, so the dock comes first
    // -- "moved to the top" for a fresh visit.
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

describe("AskTab follow-up box", () => {
  beforeEach(() => {
    mockAllHooks();
    Element.prototype.scrollTo = vi.fn();
  });

  function openResultThread(llmEnabled: boolean | undefined) {
    vi.spyOn(hooks, "useIsLlmApproved").mockReturnValue(true);
    vi.spyOn(hooks, "useFollowupEnabled").mockReturnValue({ data: { enabled: true, max_question_chars: 500 } } as never);
    vi.spyOn(config, "useFeatureOn").mockReturnValue(llmEnabled === true);
    vi.spyOn(hooks, "useConversation").mockReturnValue({
      data: {
        conversation: {
          conversation_id: "t1",
          user_id: 1,
          agency_id: 5,
          title: "T",
          filter_ctx: {},
          pinned: false,
          created_at: "",
          updated_at: "",
        },
        messages: [
          {
            message_id: 2,
            conversation_id: "t1",
            role: "assistant",
            chip_id: null,
            tool: "top_n",
            args: {},
            signature_hash: null,
            result: { kind: "table", summary: "s", rows: [["A"]], columns: ["route"] },
            rendered_summary: "Result",
            created_at: "2026-09-01T00:00:00Z",
          },
        ],
      },
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    } as never);
    renderAskTab("/agencies/5/ask?conversation=t1");
  }

  it("shows the follow-up box when the deployment has a usable LLM provider", () => {
    openResultThread(true);
    expect(screen.getByPlaceholderText("Ask about this result...")).toBeInTheDocument();
  });

  it.each([false, undefined])("hides the follow-up box when /api/config llm_enabled is %s", (llmEnabled) => {
    openResultThread(llmEnabled);
    expect(screen.queryByPlaceholderText("Ask about this result...")).not.toBeInTheDocument();
  });
});

describe("AskTab investigations", () => {
  beforeEach(() => {
    mockAllHooks();
    Element.prototype.scrollTo = vi.fn();
  });

  function openInvestigation(messages: ConvMessage[]) {
    vi.spyOn(hooks, "useConversation").mockReturnValue({
      data: {
        conversation: {
          conversation_id: "t1",
          user_id: null,
          agency_id: 5,
          title: "Morning delays",
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
    return renderAskTab("/agencies/5/ask?conversation=t1");
  }

  it("says an opened investigation has no steps yet, instead of looking unopened", () => {
    openInvestigation([]);
    expect(screen.getByText("This investigation has no steps yet. Pick an analysis card to start.")).toBeInTheDocument();
  });

  it("offers to start an analysis, not another one, before the first", () => {
    const { container } = renderAskTab();
    expect(container.querySelector(".ask-tool-menu summary")).toHaveTextContent(/^Start an analysis$/);
  });

  it("offers another analysis once there is one", () => {
    const { container } = openInvestigation([
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
    ]);
    expect(container.querySelector(".ask-tool-menu summary")).toHaveTextContent(/^Start another analysis$/);
  });
});

describe("AskTab thinking state", () => {
  beforeEach(() => {
    mockAllHooks();
    Element.prototype.scrollTo = vi.fn();
  });

  const oneMessage: ConvMessage[] = [
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

  function openInvestigation(messages: ConvMessage[]) {
    vi.spyOn(hooks, "useConversation").mockReturnValue({
      data: {
        conversation: {
          conversation_id: "t1",
          user_id: null,
          agency_id: 5,
          title: "Morning delays",
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
    return renderAskTab("/agencies/5/ask?conversation=t1");
  }

  it("shows two hidden skeleton lines alongside the accessible status text while a question is pending", () => {
    vi.spyOn(hooks, "useAppendMessage").mockReturnValue(mutationStub({ isPending: true }));
    openInvestigation(oneMessage);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Thinking...");
    expect(status).toHaveAttribute("aria-live", "polite");
    const lines = status.querySelectorAll(".skeleton");
    expect(lines).toHaveLength(2);
    for (const line of lines) {
      expect(line.closest('[aria-hidden="true"]')).not.toBeNull();
    }
    // No spinner: the pending state is a calm skeleton, not a rotating glyph.
    expect(status.querySelector("svg")).not.toBeInTheDocument();
  });

  it("renders no thinking status when nothing is pending", () => {
    openInvestigation(oneMessage);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

describe("AskTab dispatch failures", () => {
  beforeEach(() => {
    mockAllHooks();
    Element.prototype.scrollTo = vi.fn();
  });

  function firstInstantCard() {
    const landing = screen.getByText("Pick a ready-made analysis").parentElement!.parentElement!;
    const card = landing.querySelectorAll<HTMLButtonElement>("button:not([disabled])")[0];
    expect(card).toBeDefined();
    return card;
  }

  it("shows a banner with a retry when creating the thread fails, instead of vanishing silently", async () => {
    const mutateAsync = vi.fn().mockRejectedValue(new Error("boom"));
    vi.spyOn(hooks, "useCreateConversation").mockReturnValue(mutationStub({ mutateAsync }));
    renderAskTab();
    await userEvent.click(firstInstantCard());
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(mutateAsync).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(2));
  });

  it("shows a banner when appending the question to the thread fails", async () => {
    const mutate = vi.fn();
    const createMutateAsync = vi.fn().mockResolvedValue({ conversation_id: "t9" });
    vi.spyOn(hooks, "useCreateConversation").mockReturnValue(mutationStub({ mutateAsync: createMutateAsync }));
    vi.spyOn(hooks, "useAppendMessage").mockReturnValue(mutationStub({ mutate }));
    renderAskTab();
    await userEvent.click(firstInstantCard());
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    act(() => mutate.mock.calls[0][1].onError(new Error("503")));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    // The thread already exists: retrying adds the question to it rather than
    // creating a second thread.
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(2));
    expect(mutate.mock.calls[1][0].conversationId).toBe("t9");
    expect(createMutateAsync).toHaveBeenCalledTimes(1);
  });
});

describe("AskTab scope sentence", () => {
  beforeEach(() => {
    mockAllHooks();
    Element.prototype.scrollTo = vi.fn();
  });

  const region = () => screen.getByRole("region", { name: "What you're viewing" });

  it("seeds a new thread's conditions from the screen's scope", () => {
    renderAskTab("/agencies/5/ask?from=2026-09-01&to=2026-09-07&dow=weekend&time_band=morning");
    expect(screen.getByRole("button", { name: "weekends" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Morning (05–09)" })).toBeInTheDocument();
  });

  it("names the screen's conditions a thread would not keep as unused while no thread is open", () => {
    renderAskTab("/agencies/5/ask?from=2026-09-01&to=2026-09-07&stop=S1&late=120");
    expect(region()).toHaveTextContent("Not used on this screen: Stop, On-time tolerance");
  });

  it("shows an open thread's own conditions, with no tolerance clause, saved views or pin", () => {
    vi.spyOn(hooks, "useConversation").mockReturnValue({
      data: {
        conversation: {
          conversation_id: "t1", user_id: null, agency_id: 5, title: "T",
          filter_ctx: { from_date: "2026-08-01", to_date: "2026-08-14", dow: "weekday" },
          pinned: false, created_at: "", updated_at: "",
        },
        messages: [],
      },
      isPending: false, isError: false, refetch: vi.fn(),
    } as never);
    renderAskTab("/agencies/5/ask?conversation=t1&dow=weekend");
    expect(screen.getByRole("button", { name: "weekdays" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "weekends" })).toBeNull();
    expect(region()).not.toHaveTextContent("counting on-time");
    expect(screen.queryByRole("button", { name: "Show all filters" })).toBeNull();
  });
});

describe("AskTab unsaved filter edits", () => {
  beforeEach(() => {
    mockAllHooks();
    Element.prototype.scrollTo = vi.fn();
  });

  function openThread(updateMutateAsync: ReturnType<typeof vi.fn>) {
    const appendMutate = vi.fn();
    vi.spyOn(hooks, "useUpdateConversation").mockReturnValue(mutationStub({ mutateAsync: updateMutateAsync }));
    vi.spyOn(hooks, "useAppendMessage").mockReturnValue(mutationStub({ mutate: appendMutate }));
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
        messages: [],
      },
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    } as never);
    renderAskTab("/agencies/5/ask?conversation=t1");
    return { appendMutate };
  }

  async function pick(token: string, choice: string) {
    await userEvent.click(screen.getByRole("button", { name: token }));
    await userEvent.click(screen.getByRole("radio", { name: choice }));
  }

  function firstInstantCard() {
    const landing = screen.getByText("Pick a ready-made analysis").parentElement!.parentElement!;
    return landing.querySelectorAll<HTMLButtonElement>("button:not([disabled])")[0];
  }

  function deferred() {
    let resolve!: () => void;
    const promise = new Promise<void>((r) => { resolve = r; });
    return { promise, resolve };
  }

  it("keeps edits on screen and saves only the last one, once, before the next run", async () => {
    const updateMutateAsync = vi.fn().mockResolvedValue(undefined);
    const { appendMutate } = openThread(updateMutateAsync);
    await pick("every day", "Weekdays");
    await pick("all hours", "Evening (17–20)");
    await pick("weekdays", "Weekend");
    expect(screen.getByRole("button", { name: "weekends" })).toBeInTheDocument();
    expect(updateMutateAsync).not.toHaveBeenCalled();

    await userEvent.click(firstInstantCard());

    await waitFor(() => expect(appendMutate).toHaveBeenCalledTimes(1));
    expect(updateMutateAsync).toHaveBeenCalledTimes(1);
    expect(updateMutateAsync.mock.calls[0][0]).toMatchObject({
      id: "t1",
      patch: { filter_ctx: { dow: "weekend", time_band: "evening" } },
    });
    expect(updateMutateAsync.mock.invocationCallOrder[0]).toBeLessThan(appendMutate.mock.invocationCallOrder[0]);
  });

  it("does not dispatch under the previous filter when the save fails, and saves it again on the next run", async () => {
    const updateMutateAsync = vi.fn().mockRejectedValueOnce(new Error("save failed")).mockResolvedValue(undefined);
    const { appendMutate } = openThread(updateMutateAsync);
    await pick("every day", "Weekdays");

    await userEvent.click(firstInstantCard());
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(appendMutate).not.toHaveBeenCalled();

    await userEvent.click(firstInstantCard());
    await waitFor(() => expect(appendMutate).toHaveBeenCalledTimes(1));
    expect(updateMutateAsync).toHaveBeenCalledTimes(2);
    expect(updateMutateAsync.mock.calls[1][0]).toMatchObject({ id: "t1", patch: { filter_ctx: { dow: "weekday" } } });
  });

  it("saves the thread's edit when the reader leaves it for a new investigation", async () => {
    const updateMutateAsync = vi.fn().mockResolvedValue(undefined);
    openThread(updateMutateAsync);
    await pick("every day", "Weekdays");
    expect(updateMutateAsync).not.toHaveBeenCalled();

    await userEvent.click(screen.getByText("Your investigations"));
    await userEvent.click(screen.getByRole("button", { name: /New investigation/ }));

    await waitFor(() => expect(updateMutateAsync).toHaveBeenCalledTimes(1));
    expect(updateMutateAsync.mock.calls[0][0]).toMatchObject({ id: "t1", patch: { filter_ctx: { dow: "weekday" } } });
  });

  it("saves a later edit only after a slow earlier save has landed, and dispatches under the later one", async () => {
    const slow = deferred();
    const later = deferred();
    const updateMutateAsync = vi.fn().mockReturnValueOnce(slow.promise).mockReturnValueOnce(later.promise);
    const { appendMutate } = openThread(updateMutateAsync);
    await pick("every day", "Weekdays");
    await userEvent.click(firstInstantCard());
    await waitFor(() => expect(updateMutateAsync).toHaveBeenCalledTimes(1));

    await pick("weekdays", "Weekend");
    await userEvent.click(firstInstantCard());
    expect(updateMutateAsync).toHaveBeenCalledTimes(1);

    await act(async () => slow.resolve());
    await waitFor(() => expect(updateMutateAsync).toHaveBeenCalledTimes(2));
    expect(updateMutateAsync.mock.calls[1][0]).toMatchObject({ patch: { filter_ctx: { dow: "weekend" } } });
    expect(appendMutate).toHaveBeenCalledTimes(1);

    await act(async () => later.resolve());
    await waitFor(() => expect(appendMutate).toHaveBeenCalledTimes(2));
  });
});
