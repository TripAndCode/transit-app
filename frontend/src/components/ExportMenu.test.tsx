import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ExportMenu } from "./ExportMenu";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

type Row = { a: string };
const columns = [{ header: "a", value: (r: Row) => r.a }];

function renderMenu(initialPath: string, props: Partial<React.ComponentProps<typeof ExportMenu<Row>>> = {}) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <ExportMenu<Row> csv={{ filenameBase: "test", rows: [{ a: "1" }], columns }} {...props} />
    </MemoryRouter>,
  );
}

function openMenu() {
  fireEvent.click(screen.getByRole("button", { name: /export/i }));
}

describe("ExportMenu", () => {
  it("copies a link containing the current useUrlState query keys", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    renderMenu("/agencies/1/reports?from=2026-01-01&to=2026-01-31&view=saved");
    openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /copy link|link/i }));
    await vi.waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const copiedUrl = writeText.mock.calls[0][0] as string;
    expect(copiedUrl).toContain("from=2026-01-01");
    expect(copiedUrl).toContain("to=2026-01-31");
    expect(copiedUrl).toContain("view=saved");
  });

  it("falls back to a selectable input when the clipboard API is unavailable", async () => {
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined });
    renderMenu("/agencies/1/reports?view=saved");
    openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /copy link|link/i }));
    const fallbackInput = await screen.findByRole("textbox");
    expect((fallbackInput as HTMLInputElement).value).toContain("view=saved");
  });

  it("falls back to a selectable input when clipboard.writeText rejects", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    renderMenu("/agencies/1/reports?view=saved");
    openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /copy link|link/i }));
    const fallbackInput = await screen.findByRole("textbox");
    expect((fallbackInput as HTMLInputElement).value).toContain("view=saved");
  });

  it("does not render a CSV item when no csv spec is given", () => {
    renderMenu("/agencies/1/reports", { csv: null });
    openMenu();
    expect(screen.queryByRole("menuitem", { name: /csv/i })).not.toBeInTheDocument();
  });

  it("triggers a CSV download via buildCsv when the CSV item is clicked", () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderMenu("/agencies/1/reports");
    openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /csv/i }));
    expect(clickSpy).toHaveBeenCalledTimes(1);
    clickSpy.mockRestore();
  });

  it("closes the menu on Escape", () => {
    renderMenu("/agencies/1/reports");
    openMenu();
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("closes when focus leaves it entirely, as Tab off the last item does", () => {
    // Escape and an outside click both fire; walking off the end fires
    // neither, and leaves a mounted role="menu" with aria-expanded still
    // true behind a user who has already moved on.
    renderMenu("/agencies/1/reports");
    openMenu();
    const outside = document.createElement("button");
    document.body.appendChild(outside);

    fireEvent.blur(screen.getByRole("menu"), { relatedTarget: outside });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /export/i })).toHaveAttribute("aria-expanded", "false");
    outside.remove();
  });

  it("stays open while focus moves between its own items", () => {
    renderMenu("/agencies/1/reports");
    openMenu();
    const items = screen.getAllByRole("menuitem");

    fireEvent.blur(screen.getByRole("menu"), { relatedTarget: items[1] });
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("moves focus onto the first item when the menu opens", () => {
    renderMenu("/agencies/1/reports");
    openMenu();
    expect(screen.getAllByRole("menuitem")[0]).toHaveFocus();
  });

  it("walks the items with the arrow keys, wrapping at both ends", () => {
    renderMenu("/agencies/1/reports");
    openMenu();
    const items = screen.getAllByRole("menuitem");
    expect(items.length).toBeGreaterThan(1);

    fireEvent.keyDown(items[0], { key: "ArrowDown" });
    expect(items[1]).toHaveFocus();
    fireEvent.keyDown(items[1], { key: "ArrowUp" });
    expect(items[0]).toHaveFocus();
    fireEvent.keyDown(items[0], { key: "ArrowUp" });
    expect(items[items.length - 1]).toHaveFocus();
    fireEvent.keyDown(items[items.length - 1], { key: "ArrowDown" });
    expect(items[0]).toHaveFocus();
  });

  it("jumps to the first and last item with Home and End", () => {
    renderMenu("/agencies/1/reports");
    openMenu();
    const items = screen.getAllByRole("menuitem");
    fireEvent.keyDown(items[0], { key: "End" });
    expect(items[items.length - 1]).toHaveFocus();
    fireEvent.keyDown(items[items.length - 1], { key: "Home" });
    expect(items[0]).toHaveFocus();
  });

  it("returns focus to the trigger when Escape closes the menu", () => {
    renderMenu("/agencies/1/reports");
    const trigger = screen.getByRole("button", { name: /export/i });
    openMenu();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
