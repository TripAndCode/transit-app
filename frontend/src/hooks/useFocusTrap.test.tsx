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
});
