import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { Modal } from "./Modal";

function Harness({ initialOpen = true }: { initialOpen?: boolean }) {
  const [open, setOpen] = useState(initialOpen);
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>
        open trigger
      </button>
      <Modal open={open} onClose={() => setOpen(false)} ariaLabel="Example modal">
        <button type="button">first</button>
        <button type="button">second</button>
        <button type="button">last</button>
      </Modal>
    </div>
  );
}

describe("Modal", () => {
  afterEach(() => {
    document.body.style.overflow = "";
  });

  it("renders nothing when closed", () => {
    render(<Modal open={false} onClose={() => {}} ariaLabel="Hidden">content</Modal>);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders a dialog with aria-modal when open", () => {
    render(<Modal open onClose={() => {}} ariaLabel="Example">content</Modal>);
    const dialog = screen.getByRole("dialog", { name: "Example" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
  });

  it("supports labelledBy as an alternative to ariaLabel", () => {
    render(
      <Modal open onClose={() => {}} labelledBy="modal-heading">
        <h2 id="modal-heading">Heading title</h2>
      </Modal>,
    );
    expect(screen.getByRole("dialog", { name: "Heading title" })).toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} ariaLabel="Example">
        <button type="button">inside</button>
      </Modal>,
    );
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on backdrop click but not on click inside the panel", async () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} ariaLabel="Example">
        <button type="button">inside</button>
      </Modal>,
    );
    await userEvent.click(screen.getByRole("button", { name: "inside" }));
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("presentation"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("moves focus into the dialog on open", () => {
    render(
      <Modal open onClose={() => {}} ariaLabel="Example">
        <button type="button">first</button>
      </Modal>,
    );
    expect(screen.getByRole("dialog")).toHaveFocus();
  });

  it("moves focus to initialFocusRef when provided", () => {
    function WithInitialFocus() {
      const ref = { current: null } as React.RefObject<HTMLButtonElement | null>;
      return (
        <Modal open onClose={() => {}} ariaLabel="Example" initialFocusRef={ref}>
          <button type="button">skip</button>
          <button type="button" ref={ref}>
            focus me
          </button>
        </Modal>
      );
    }
    render(<WithInitialFocus />);
    expect(screen.getByRole("button", { name: "focus me" })).toHaveFocus();
  });

  it("traps Tab focus between the first and last focusable elements", async () => {
    render(
      <Modal open onClose={() => {}} ariaLabel="Example">
        <button type="button">first</button>
        <button type="button">middle</button>
        <button type="button">last</button>
      </Modal>,
    );
    const last = screen.getByRole("button", { name: "last" });
    last.focus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "first" })).toHaveFocus();

    await userEvent.tab({ shift: true });
    expect(last).toHaveFocus();
  });

  it("restores focus to the previously focused element on close", async () => {
    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "open trigger" });
    trigger.focus();
    // Re-render closed by simulating Escape (Harness owns open state).
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("locks body scroll while open and restores it on close", async () => {
    const { rerender } = render(
      <Modal open onClose={() => {}} ariaLabel="Example">
        <button type="button">inside</button>
      </Modal>,
    );
    expect(document.body.style.overflow).toBe("hidden");
    rerender(
      <Modal open={false} onClose={() => {}} ariaLabel="Example">
        <button type="button">inside</button>
      </Modal>,
    );
    expect(document.body.style.overflow).not.toBe("hidden");
  });
});
