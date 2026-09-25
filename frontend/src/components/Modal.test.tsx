import { describe, expect, it, vi, afterEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import { Modal } from "./Modal";
import { useFocusTrap } from "../hooks/useFocusTrap";
import { Z_INDEX } from "../styles/zIndex";

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

  it("renders through the shared overlay base", () => {
    render(
      <Modal open onClose={() => {}} ariaLabel="Example">
        content
      </Modal>,
    );
    expect(screen.getByRole("dialog")).toHaveClass("ui-overlay-panel");
    expect(screen.getByRole("presentation")).toHaveClass("ui-overlay-scrim");
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

describe("stacked over another trapped surface", () => {
  function Stacked({ onSheetEscape, onModalClose }: { onSheetEscape: () => void; onModalClose: () => void }) {
    const sheetRef = useRef<HTMLDivElement>(null);
    const [modalOpen, setModalOpen] = useState(false);
    useFocusTrap(true, sheetRef, onSheetEscape);
    return (
      <div ref={sheetRef} tabIndex={-1}>
        <button type="button" onClick={() => setModalOpen(true)}>
          open modal
        </button>
        <Modal open={modalOpen} onClose={onModalClose} ariaLabel="On top">
          <button type="button">inside modal</button>
        </Modal>
      </div>
    );
  }

  it("lets one Escape close only the surface on top", async () => {
    // Both listen on `document`, where stopPropagation does not reach a
    // sibling listener on the same target -- so a modal that does not join
    // the activation stack takes the sheet underneath down with it.
    const onSheetEscape = vi.fn();
    const onModalClose = vi.fn();
    const user = userEvent.setup();
    render(<Stacked onSheetEscape={onSheetEscape} onModalClose={onModalClose} />);
    await user.click(screen.getByRole("button", { name: "open modal" }));

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onModalClose).toHaveBeenCalledTimes(1);
    expect(onSheetEscape).not.toHaveBeenCalled();
  });
});

describe("stacking", () => {
  it("puts a drawer on the drawer rungs and a modal on the modal rungs", () => {
    // The ladder separates the two so a modal opened over a drawer layers
    // above it. Pinning both variants to the modal rungs leaves DOM order
    // to decide, which is the thing the ladder exists to stop.
    const { unmount } = render(
      <Modal open onClose={() => {}} ariaLabel="drawer" variant="drawer">
        <p>body</p>
      </Modal>,
    );
    const drawerPanel = screen.getByRole("dialog");
    expect(drawerPanel.style.zIndex).toBe(String(Z_INDEX.drawer));
    expect((drawerPanel.parentElement as HTMLElement).style.zIndex).toBe(String(Z_INDEX.drawerBackdrop));
    unmount();

    render(
      <Modal open onClose={() => {}} ariaLabel="modal">
        <p>body</p>
      </Modal>,
    );
    const modalPanel = screen.getByRole("dialog");
    expect(modalPanel.style.zIndex).toBe(String(Z_INDEX.modal));
    expect((modalPanel.parentElement as HTMLElement).style.zIndex).toBe(String(Z_INDEX.modalBackdrop));
  });
});
