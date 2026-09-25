import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  it("searches titles and pattern codes, preserving pinned grouping", () => {
    mockConversations([
      conv({ title: "Morning delays", pinned: true, filter_ctx: { routes: ["C10"] } }),
      conv({ conversation_id: "c2", title: "Evening service" }),
    ]);
    render();
    const search = screen.getByRole("searchbox");
    fireEvent.change(search, { target: { value: "ｃ１０" } });
    expect(screen.getByText("Morning delays")).toBeInTheDocument();
    expect(screen.queryByText("Evening service")).not.toBeInTheDocument();
    expect(screen.getByText("📌 Pinned")).toBeInTheDocument();
    fireEvent.change(search, { target: { value: "missing" } });
    expect(screen.getByRole("status")).toHaveTextContent("No matching investigations");
    fireEvent.change(search, { target: { value: "" } });
    expect(screen.getByText("Evening service")).toBeInTheDocument();
  });
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

  it("renders a single embedded copy of the sidebar content, with no viewport-specific chrome", () => {
    mockConversations([]);
    render();
    expect(screen.getByText("New conversation")).toBeInTheDocument();
  });

  describe("row semantics", () => {
    it("exposes the main row action as a real, independently reachable button, not a role=button wrapper around the kebab", () => {
      mockConversations([conv({ title: "Morning delays" })]);
      render();
      const rowButton = screen.getByRole("button", { name: /Morning delays/ });
      expect(rowButton.tagName.toLowerCase()).toBe("button");
      const kebab = screen.getByRole("button", { name: "More options" });
      // The kebab must be a sibling, not a descendant of the row button --
      // a real <button> cannot validly contain another interactive control.
      expect(rowButton.contains(kebab)).toBe(false);
    });

    it("selects the conversation when the row button is activated by keyboard", async () => {
      const onSelect = vi.fn();
      mockConversations([conv({ title: "Morning delays" })]);
      renderWithProviders(
        <ThreadSidebar agencyId={9} activeId={null} onSelect={onSelect} onNewThread={vi.fn()} />,
      );
      await userEvent.tab(); // New-thread button
      await userEvent.tab(); // search input
      await userEvent.tab(); // row button
      const rowButton = screen.getByRole("button", { name: /Morning delays/ });
      expect(rowButton).toHaveFocus();
      await userEvent.keyboard("{Enter}");
      expect(onSelect).toHaveBeenCalledWith("c1");
    });

    it("still opens the context menu on right-click of the row container", () => {
      mockConversations([conv({ title: "Morning delays" })]);
      render();
      const rowButton = screen.getByRole("button", { name: /Morning delays/ });
      fireEvent.contextMenu(rowButton);
      expect(screen.getByText("Rename")).toBeInTheDocument();
    });
  });

  describe("context menu", () => {
    it("is a menu of menuitems and takes focus when it opens", () => {
      mockConversations([conv({ title: "Morning delays" })]);
      render();
      fireEvent.click(screen.getByRole("button", { name: "More options" }));
      const menu = screen.getByRole("menu");
      const items = within(menu).getAllByRole("menuitem");
      expect(items.map((i) => i.textContent)).toEqual(["Rename", "Pin", "Delete"]);
      expect(items[0]).toHaveFocus();
    });

    it("walks its items with the arrow keys, wrapping at both ends", () => {
      mockConversations([conv({ title: "Morning delays" })]);
      render();
      fireEvent.click(screen.getByRole("button", { name: "More options" }));
      const items = within(screen.getByRole("menu")).getAllByRole("menuitem");

      fireEvent.keyDown(items[0], { key: "ArrowDown" });
      expect(items[1]).toHaveFocus();
      fireEvent.keyDown(items[1], { key: "ArrowUp" });
      expect(items[0]).toHaveFocus();
      fireEvent.keyDown(items[0], { key: "ArrowUp" });
      expect(items[2]).toHaveFocus();
      fireEvent.keyDown(items[2], { key: "ArrowDown" });
      expect(items[0]).toHaveFocus();
    });

    it("returns focus to the control that opened it when Escape closes it", () => {
      mockConversations([conv({ title: "Morning delays" })]);
      render();
      const kebab = screen.getByRole("button", { name: "More options" });
      fireEvent.click(kebab);
      fireEvent.keyDown(document, { key: "Escape" });
      expect(screen.queryByRole("menu")).not.toBeInTheDocument();
      expect(kebab).toHaveFocus();
    });

    it("returns focus to the control that opened it when an item is chosen", () => {
      // Choosing an item unmounts the menuitem that had focus. Escape is not
      // the only exit that has to put the operator back on the kebab.
      mockConversations([conv({ title: "Morning delays" })]);
      render();
      const kebab = screen.getByRole("button", { name: "More options" });
      fireEvent.click(kebab);
      const pin = within(screen.getByRole("menu"))
        .getAllByRole("menuitem")
        .find((item) => /pin/i.test(item.textContent ?? ""))!;

      fireEvent.click(pin);
      expect(screen.queryByRole("menu")).not.toBeInTheDocument();
      expect(kebab).toHaveFocus();
    });

    it("styles its items by class rather than by mutating inline style on hover", () => {
      mockConversations([conv({ title: "Morning delays" })]);
      render();
      fireEvent.click(screen.getByRole("button", { name: "More options" }));
      const item = within(screen.getByRole("menu")).getAllByRole("menuitem")[0];
      expect(item).toHaveClass("context-menu__item");
      fireEvent.mouseEnter(item);
      expect(item.style.background).toBe("");
    });

    it("closes on Escape", () => {
      mockConversations([conv({ title: "Morning delays" })]);
      render();
      fireEvent.contextMenu(screen.getByRole("button", { name: /Morning delays/ }));
      expect(screen.getByText("Rename")).toBeInTheDocument();
      fireEvent.keyDown(document, { key: "Escape" });
      expect(screen.queryByText("Rename")).not.toBeInTheDocument();
    });

    it("clamps its position so it never renders past the right/bottom viewport edge", () => {
      const originalInnerWidth = window.innerWidth;
      const originalInnerHeight = window.innerHeight;
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 400 });
      Object.defineProperty(window, "innerHeight", { configurable: true, value: 300 });
      const offsetWidthSpy = vi
        .spyOn(HTMLElement.prototype, "offsetWidth", "get")
        .mockReturnValue(200);
      const offsetHeightSpy = vi
        .spyOn(HTMLElement.prototype, "offsetHeight", "get")
        .mockReturnValue(150);

      mockConversations([conv({ title: "Morning delays" })]);
      render();
      const kebab = screen.getByRole("button", { name: "More options" });
      vi.spyOn(kebab, "getBoundingClientRect").mockReturnValue({
        right: 395,
        top: 290,
        left: 350,
        bottom: 300,
        width: 24,
        height: 24,
        x: 350,
        y: 290,
        toJSON() {},
      } as DOMRect);
      fireEvent.click(kebab);

      const menu = screen.getByText("Rename").closest("div") as HTMLElement;
      expect(parseFloat(menu.style.left)).toBeLessThanOrEqual(400 - 200 - 1);
      expect(parseFloat(menu.style.top)).toBeLessThanOrEqual(300 - 150 - 1);

      offsetWidthSpy.mockRestore();
      offsetHeightSpy.mockRestore();
      Object.defineProperty(window, "innerWidth", { configurable: true, value: originalInnerWidth });
      Object.defineProperty(window, "innerHeight", { configurable: true, value: originalInnerHeight });
    });
  });
});
