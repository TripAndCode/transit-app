import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import { conversationsAnon } from "../api/conversationsAnon";
import { resetThreadFilterSaves } from "../api/threadFilterSaver";
import type { Conversation } from "../api/types";
import { AskTab } from "./AskTab";

const session = vi.hoisted(() => ({ signedIn: true }));
const client = vi.hoisted(() => ({
  patches: [] as { path: string; body: { filter_ctx: unknown }; resolve: () => void; reject: (e: unknown) => void }[],
  autoResolve: false,
  /** The thread's filter as the server holds it: the last PATCH that landed. */
  stored: {} as unknown,
}));

vi.mock("../api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/auth")>()),
  useSession: () => ({ data: session.signedIn ? { user_id: 1 } : null, isPending: false }),
}));

const THREAD: Conversation = {
  conversation_id: "t1",
  user_id: 1,
  agency_id: 5,
  title: "Thread one",
  filter_ctx: {},
  pinned: false,
  created_at: "2026-11-16T00:00:00Z",
  updated_at: "2026-11-16T00:00:00Z",
};

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    apiGet: vi.fn((path: string) => {
      if (path === "/api/5/conversations/t1") return Promise.resolve({ ...THREAD, filter_ctx: client.stored });
      if (path === "/api/5/conversations/t1/messages") return Promise.resolve([]);
      if (path.startsWith("/api/5/conversations")) return Promise.resolve([THREAD]);
      return new Promise(() => {});
    }),
    apiPatch: vi.fn(
      (path: string, body: { filter_ctx: unknown }) =>
        new Promise((resolve, reject) => {
          const land = () => {
            client.stored = body.filter_ctx;
            resolve({ ...THREAD, filter_ctx: body.filter_ctx });
          };
          client.patches.push({ path, body, resolve: land, reject });
          if (client.autoResolve) land();
        }),
    ),
    apiDelete: vi.fn(() => Promise.resolve({ ok: true })),
  };
});

function stub(overrides: Record<string, unknown> = {}) {
  return { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false, isError: false, error: null, reset: vi.fn(), ...overrides } as never;
}

let appendMutate: ReturnType<typeof vi.fn>;

beforeEach(() => {
  session.signedIn = true;
  client.patches = [];
  client.autoResolve = false;
  client.stored = {};
  localStorage.clear();
  Element.prototype.scrollTo = vi.fn();
  appendMutate = vi.fn();
  vi.spyOn(hooks, "useAppendMessage").mockReturnValue(stub({ mutate: appendMutate }));
  vi.spyOn(hooks, "useCreateConversation").mockReturnValue(stub());
  vi.spyOn(hooks, "useFollowup").mockReturnValue(stub());
  vi.spyOn(hooks, "useFollowupEnabled").mockReturnValue({ data: { enabled: false, max_question_chars: 500 } } as never);
  vi.spyOn(hooks, "useIsLlmApproved").mockReturnValue(false);
  vi.spyOn(hooks, "useMigrateAnon").mockReturnValue(stub());
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
  vi.spyOn(conversationsAnon, "exportAll").mockReturnValue([]);
});

afterEach(() => {
  vi.restoreAllMocks();
  resetThreadFilterSaves();
});

function tree(conversation = "t1") {
  return (
    <MemoryRouter initialEntries={[`/agencies/5/ask?conversation=${conversation}`]}>
      <Routes>
        <Route path="/agencies/:agencyId/ask" element={<AskTab />} />
      </Routes>
    </MemoryRouter>
  );
}

async function pick(token: string, choice: string) {
  await userEvent.click(await screen.findByRole("button", { name: token }));
  await userEvent.click(screen.getByRole("radio", { name: choice }));
}

function runCard() {
  const landing = screen.getByText("Pick a ready-made analysis").parentElement!.parentElement!;
  return landing.querySelectorAll<HTMLButtonElement>("button:not([disabled])")[0];
}

const patchedFilters = () => client.patches.map((p) => p.body.filter_ctx);

async function openHistory() {
  await userEvent.click(screen.getByText("Your investigations"));
  return screen.getByRole("button", { name: /Thread one/ });
}

describe("AskTab thread filter saves", () => {
  it("saves a burst of edits as at most two PATCHes, ending with the last value", async () => {
    renderWithProviders(tree());
    await pick("every day", "Weekdays");
    await pick("all hours", "Evening (17–20)");
    await pick("weekdays", "Weekend");
    expect(screen.getByRole("button", { name: "weekends" })).toBeInTheDocument();
    expect(patchedFilters()).toEqual([expect.objectContaining({ dow: "weekday" })]);

    await act(async () => client.patches[0].resolve());
    await waitFor(() => expect(client.patches).toHaveLength(2));
    expect(patchedFilters()[1]).toMatchObject({ dow: "weekend", time_band: "evening" });
    await act(async () => client.patches[1].resolve());
    expect(client.patches).toHaveLength(2);
  });

  it("shows, after a re-pick of the open thread, the filter the dispatch then saves", async () => {
    renderWithProviders(tree());
    await pick("every day", "Weekdays");
    await userEvent.click(await openHistory());
    expect(screen.getByRole("button", { name: "weekdays" })).toBeInTheDocument();

    await userEvent.click(runCard());
    expect(appendMutate).not.toHaveBeenCalled();
    await act(async () => client.patches[0].resolve());
    await waitFor(() => expect(appendMutate).toHaveBeenCalledTimes(1));
    expect(patchedFilters().at(-1)).toMatchObject({ dow: "weekday" });
    expect(screen.getByRole("button", { name: "weekdays" })).toBeInTheDocument();
  });

  it("raises no alert when the open thread is deleted while its save is in flight", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithProviders(tree());
    await pick("every day", "Weekdays");
    const history = await openHistory();
    await userEvent.click(within(history.parentElement!).getByRole("button", { name: "More options" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    await act(async () => client.patches[0].reject(new Error("404")));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps a remounted screen's save behind the one still in flight", async () => {
    const first = renderWithProviders(tree());
    await pick("every day", "Weekdays");
    first.unmount();

    renderWithProviders(tree());
    await pick("weekdays", "Weekend");
    expect(client.patches).toHaveLength(1);
    await act(async () => client.patches[0].resolve());
    await waitFor(() => expect(client.patches).toHaveLength(2));
    expect(patchedFilters()).toEqual([expect.objectContaining({ dow: "weekday" }), expect.objectContaining({ dow: "weekend" })]);
  });

  it("writes a signed-out reader's edit to this browser at once", async () => {
    session.signedIn = false;
    const thread = conversationsAnon.create(5, "Local thread");
    renderWithProviders(tree(thread.client_id));
    await pick("every day", "Weekdays");
    expect(conversationsAnon.get(thread.client_id, 5)?.filter_ctx).toMatchObject({ dow: "weekday" });
  });

  it("blocks the dispatch when the save failed, and its retry saves again and asks once", async () => {
    renderWithProviders(tree());
    await pick("every day", "Weekdays");
    await act(async () => client.patches[0].reject(new Error("save failed")));

    await userEvent.click(runCard());
    await waitFor(() => expect(client.patches).toHaveLength(2));
    await act(async () => client.patches[1].reject(new Error("save failed again")));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(appendMutate).not.toHaveBeenCalled();

    client.autoResolve = true;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(appendMutate).toHaveBeenCalledTimes(1));
    expect(patchedFilters()).toHaveLength(3);
    expect(patchedFilters()[2]).toMatchObject({ dow: "weekday" });
  });

  it("sends one question for a double press while the save is still landing", async () => {
    renderWithProviders(tree());
    await pick("every day", "Weekdays");
    const card = runCard();
    fireEvent.click(card);
    fireEvent.click(card);
    await act(async () => client.patches[0].resolve());
    await waitFor(() => expect(appendMutate).toHaveBeenCalledTimes(1));
    await act(async () => {});
    expect(appendMutate).toHaveBeenCalledTimes(1);
  });
});
