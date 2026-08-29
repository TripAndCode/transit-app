import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { ThreadSidebar } from "./ThreadSidebar";
import * as hooks from "../api/hooks";
import type { Conversation } from "../api/types";

function conv(over: Partial<Conversation>): Conversation {
  return {
    conversation_id: "c1",
    user_id: null,
    agency_id: 9,
    title: "Untitled",
    filter_ctx: { dow: "all", time_band: "all", service: "all", routes: [] },
    pinned: false,
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
    ...over,
  };
}

function mockConversations(data: Conversation[], isLoading = false) {
  vi.spyOn(hooks, "useConversations").mockReturnValue({ data, isLoading } as never);
  vi.spyOn(hooks, "useUpdateConversation").mockReturnValue({ mutate: vi.fn() } as never);
  vi.spyOn(hooks, "useDeleteConversation").mockReturnValue({ mutate: vi.fn() } as never);
}

function render() {
  renderWithProviders(
    <ThreadSidebar agencyId={9} activeId={null} onSelect={vi.fn()} onNewThread={vi.fn()} />,
  );
}

describe("ThreadSidebar", () => {
  it("shows the empty state when there are no conversations", () => {
    mockConversations([]);
    render();
    expect(screen.getAllByText("No conversations yet").length).toBeGreaterThan(0);
  });

  it("shows the loading state", () => {
    mockConversations([], true);
    render();
    expect(screen.getAllByText("Loading...").length).toBeGreaterThan(0);
  });

  it("renders a pinned conversation under a Pinned header with the pin emoji, before date groups", () => {
    const now = new Date().toISOString();
    mockConversations([
      conv({ conversation_id: "pinned-1", title: "Pinned thread", pinned: true, updated_at: now }),
      conv({ conversation_id: "today-1", title: "Today thread", pinned: false, updated_at: now }),
    ]);
    render();
    const headers = screen.getAllByText(/Pinned|Today/);
    // Pinned section must render before the Today group, and include the pin emoji.
    const pinnedHeader = headers.find((h) => h.textContent?.includes("Pinned"));
    const todayHeader = headers.find((h) => h.textContent === "Today");
    expect(pinnedHeader?.textContent).toBe("📌 Pinned");
    expect(todayHeader).toBeDefined();
    const pinnedIndex = headers.indexOf(pinnedHeader!);
    const todayIndex = headers.indexOf(todayHeader!);
    expect(pinnedIndex).toBeLessThan(todayIndex);

    expect(screen.getAllByText("Pinned thread").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Today thread").length).toBeGreaterThan(0);
  });

  it("does not render a Pinned header when there are no pinned conversations", () => {
    mockConversations([conv({ conversation_id: "today-1", title: "Today thread", updated_at: new Date().toISOString() })]);
    render();
    expect(screen.queryAllByText(/^📌 Pinned$/).length).toBe(0);
  });

  it("renders only the desktop variant (not also a hidden mobile copy) on a wide viewport", () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({
      matches: false,
      media: "(max-width: 640px)",
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    } as unknown as MediaQueryList);
    mockConversations([]);
    render();
    // Previously both the desktop <aside> and the mobile drawer mounted
    // unconditionally (toggled only via a CSS display media query), so the
    // "New conversation" control existed twice in the DOM at every viewport
    // width -- getByText throws on more than one match, so this fails loudly
    // if that regresses.
    expect(screen.getByText("New conversation")).toBeInTheDocument();
    expect(screen.queryByLabelText("New conversation")).not.toBeInTheDocument();
  });

  it("renders only the mobile variant (hamburger + drawer) on a narrow viewport", () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({
      matches: true,
      media: "(max-width: 640px)",
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    } as unknown as MediaQueryList);
    mockConversations([]);
    render();
    expect(screen.getByLabelText("New conversation")).toBeInTheDocument(); // hamburger button
    expect(screen.getByText("New conversation")).toBeInTheDocument(); // button inside the drawer
  });
});
