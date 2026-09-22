import { useRef, useState } from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useFocusTrap } from "./useFocusTrap";

function Harness({ onEscape }: { onEscape: () => void }) {
  const [active, setActive] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  useFocusTrap(active, containerRef, onEscape);
  return (
    <div>
      <button type="button" onClick={() => setActive(true)}>
        outside trigger
      </button>
      {active && (
        <div ref={containerRef} tabIndex={-1}>
          <button type="button">first</button>
          <button type="button">last</button>
        </div>
      )}
    </div>
  );
}

describe("useFocusTrap", () => {
  it("moves focus to the first focusable descendant once active", async () => {
    const user = userEvent.setup();
    render(<Harness onEscape={() => {}} />);
    await user.click(screen.getByText("outside trigger"));
    expect(screen.getByText("first")).toHaveFocus();
  });

  it("wraps Tab from the last descendant back to the first", async () => {
    const user = userEvent.setup();
    render(<Harness onEscape={() => {}} />);
    await user.click(screen.getByText("outside trigger"));
    screen.getByText("last").focus();
    await user.tab();
    expect(screen.getByText("first")).toHaveFocus();
  });

  it("wraps Shift+Tab from the first descendant back to the last", async () => {
    const user = userEvent.setup();
    render(<Harness onEscape={() => {}} />);
    await user.click(screen.getByText("outside trigger"));
    expect(screen.getByText("first")).toHaveFocus();
    await user.tab({ shift: true });
    expect(screen.getByText("last")).toHaveFocus();
  });

  it("invokes onEscape when Escape is pressed while active", async () => {
    const onEscape = vi.fn();
    const user = userEvent.setup();
    render(<Harness onEscape={onEscape} />);
    await user.click(screen.getByText("outside trigger"));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onEscape).toHaveBeenCalledTimes(1);
  });

  it("restores focus to the previously focused element once deactivated", async () => {
    function Restorable() {
      const [active, setActive] = useState(false);
      const containerRef = useRef<HTMLDivElement>(null);
      useFocusTrap(active, containerRef, () => {});
      return (
        <div>
          <button type="button" onClick={() => setActive(true)}>
            trigger
          </button>
          {active && (
            <div ref={containerRef} tabIndex={-1}>
              <button type="button" onClick={() => setActive(false)}>
                close
              </button>
            </div>
          )}
        </div>
      );
    }
    const user = userEvent.setup();
    render(<Restorable />);
    const trigger = screen.getByText("trigger");
    await user.click(trigger);
    await user.click(screen.getByText("close"));
    expect(trigger).toHaveFocus();
  });

  it("only lets the topmost of two concurrent traps act on Escape", async () => {
    // Both traps listen on `document`, where stopPropagation does not reach
    // a sibling listener -- so without a stack one Escape closes both.
    function Nested({ onOuter, onInner }: { onOuter: () => void; onInner: () => void }) {
      const [innerOpen, setInnerOpen] = useState(false);
      const outerRef = useRef<HTMLDivElement>(null);
      const innerRef = useRef<HTMLDivElement>(null);
      useFocusTrap(true, outerRef, onOuter);
      useFocusTrap(innerOpen, innerRef, onInner);
      return (
        <div>
          <div ref={outerRef} tabIndex={-1}>
            <button type="button" onClick={() => setInnerOpen(true)}>
              open inner
            </button>
          </div>
          {innerOpen && (
            <div ref={innerRef} tabIndex={-1}>
              <button type="button">inner content</button>
            </div>
          )}
        </div>
      );
    }
    const onOuter = vi.fn();
    const onInner = vi.fn();
    const user = userEvent.setup();
    render(<Nested onOuter={onOuter} onInner={onInner} />);
    await user.click(screen.getByText("open inner"));

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onInner).toHaveBeenCalledTimes(1);
    expect(onOuter).not.toHaveBeenCalled();
  });

  it("locks the page behind it from scrolling, and restores it on deactivation", async () => {
    const user = userEvent.setup();
    document.body.style.overflow = "auto";
    const { unmount } = render(<Harness onEscape={() => {}} />);

    await user.click(screen.getByText("outside trigger"));
    expect(document.body.style.overflow).toBe("hidden");

    unmount();
    expect(document.body.style.overflow).toBe("auto");
  });
});
