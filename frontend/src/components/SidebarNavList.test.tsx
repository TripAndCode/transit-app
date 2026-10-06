import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { SidebarNavList } from "./SidebarNavList";

const ITEMS = [
  { key: "a", label: "First section" },
  { key: "b", label: "Second section, long enough to wrap onto two lines" },
];

function rect(top: number, height: number): DOMRect {
  return { top, height, left: 0, width: 240, bottom: top + height, right: 240, x: 0, y: top, toJSON: () => ({}) } as DOMRect;
}

function renderList(activeKey: string | null) {
  return render(<SidebarNavList ariaLabel="Sections" width={240} items={ITEMS} activeKey={activeKey} onSelect={vi.fn()} />);
}

describe("SidebarNavList", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("marks the current entry by colour, not weight, so a long title keeps its line breaks", () => {
    renderList("b");
    const current = screen.getByRole("button", { name: /Second section/ });
    const other = screen.getByRole("button", { name: "First section" });
    expect(current).toHaveAttribute("aria-current", "true");
    expect(current.style.fontWeight).toBe(other.style.fontWeight);
    expect(current.style.color).toBe("var(--accent-strong)");
  });

  it("draws the current entry's fill as one highlight that follows the current entry", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      if (this.tagName === "NAV") return rect(100, 120);
      if (this.textContent === "First section") return rect(100, 40);
      if (this.textContent?.startsWith("Second section")) return rect(140, 60);
      return rect(0, 0);
    });
    const { rerender } = renderList("a");
    const indicator = document.querySelector<HTMLElement>(".nav-indicator")!;
    expect(indicator).toHaveAttribute("aria-hidden", "true");
    expect([indicator.style.transform, indicator.style.height]).toEqual(["translateY(0px)", "40px"]);
    rerender(<SidebarNavList ariaLabel="Sections" width={240} items={ITEMS} activeKey="b" onSelect={vi.fn()} />);
    expect([indicator.style.transform, indicator.style.height]).toEqual(["translateY(40px)", "60px"]);
    expect(screen.getByRole("button", { name: /Second section/ }).style.background).toBe("transparent");
  });
});
