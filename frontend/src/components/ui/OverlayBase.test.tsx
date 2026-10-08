import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi, afterEach } from "vitest";
import { useRef, useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OverlayBase } from "./OverlayBase";

const ui = readFileSync(resolve(process.cwd(), "src/components/ui/ui.css"), "utf8");

function Harness({ modal = true }: { modal?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>
        open trigger
      </button>
      <OverlayBase open={open} onClose={() => setOpen(false)} ariaLabel="Example" modal={modal} scrim={modal}>
        <button type="button">inside</button>
      </OverlayBase>
    </div>
  );
}

describe("OverlayBase", () => {
  afterEach(() => {
    document.body.style.overflow = "";
  });

  it("renders nothing while closed", () => {
    render(
      <OverlayBase open={false} onClose={() => {}} ariaLabel="Example">
        body
      </OverlayBase>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("presentation")).toBeNull();
  });

  it("renders the panel through a portal, outside the caller's own subtree", () => {
    const { container } = render(
      <OverlayBase open onClose={() => {}} ariaLabel="Example">
        body
      </OverlayBase>,
    );
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(screen.getByRole("dialog", { name: "Example" })).toBeInTheDocument();
  });

  it("renders in place when the overlay is positioned inside a page region", () => {
    const { container } = render(
      <OverlayBase open onClose={() => {}} ariaLabel="Example" portal={false} scrim={false}>
        body
      </OverlayBase>,
    );
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
  });

  it("paints the scrim with the themed --scrim token, not a literal", () => {
    render(
      <OverlayBase open onClose={() => {}} ariaLabel="Example" scrimZIndex={300}>
        body
      </OverlayBase>,
    );
    const scrim = screen.getByRole("presentation");
    expect(scrim.style.background).toBe("var(--scrim)");
    expect(scrim.style.zIndex).toBe("300");
  });

  it("omits the scrim entirely when the overlay does not cover the page", () => {
    render(
      <OverlayBase open onClose={() => {}} ariaLabel="Example" scrim={false}>
        body
      </OverlayBase>,
    );
    expect(screen.queryByRole("presentation")).toBeNull();
  });

  it("marks a modal overlay aria-modal and locks body scroll", () => {
    const { rerender } = render(
      <OverlayBase open onClose={() => {}} ariaLabel="Example">
        <button type="button">inside</button>
      </OverlayBase>,
    );
    expect(screen.getByRole("dialog")).toHaveAttribute("aria-modal", "true");
    expect(document.body.style.overflow).toBe("hidden");
    rerender(
      <OverlayBase open={false} onClose={() => {}} ariaLabel="Example">
        <button type="button">inside</button>
      </OverlayBase>,
    );
    expect(document.body.style.overflow).not.toBe("hidden");
  });

  it("leaves a non-modal overlay off aria-modal and lets the page keep scrolling", () => {
    render(
      <OverlayBase open onClose={() => {}} ariaLabel="Example" modal={false} scrim={false}>
        <button type="button">inside</button>
      </OverlayBase>,
    );
    expect(screen.getByRole("dialog")).not.toHaveAttribute("aria-modal");
    expect(document.body.style.overflow).not.toBe("hidden");
  });

  it("traps Tab inside a modal overlay but not a non-modal one", async () => {
    const user = userEvent.setup();
    const { unmount } = render(
      <div>
        <button type="button">outside</button>
        <OverlayBase open onClose={() => {}} ariaLabel="Modal">
          <button type="button">only</button>
        </OverlayBase>
      </div>,
    );
    screen.getByRole("button", { name: "only" }).focus();
    await user.tab();
    expect(screen.getByRole("button", { name: "only" })).toHaveFocus();
    unmount();

    render(
      <div>
        <button type="button">outside</button>
        <OverlayBase open onClose={() => {}} ariaLabel="Non-modal" modal={false} scrim={false} portal={false}>
          <button type="button">only</button>
        </OverlayBase>
      </div>,
    );
    screen.getByRole("button", { name: "only" }).focus();
    await user.tab();
    expect(screen.getByRole("button", { name: "only" })).not.toHaveFocus();
  });

  it.each([true, false])("closes on Escape (modal=%s)", async (modal) => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <OverlayBase open onClose={onClose} ariaLabel="Example" modal={modal} scrim={modal}>
        <button type="button">inside</button>
      </OverlayBase>,
    );
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("leaves a non-modal overlay open when Escape dismisses a modal one stacked over it", async () => {
    // The admin pages do exactly this: a detail drawer stays open while the
    // operator edits a row in a modal. Both listen on `document`, where
    // `stopPropagation` never reaches a sibling listener, so the
    // non-modal surface has to check the stack too.
    const user = userEvent.setup();
    const closeNonModal = vi.fn();
    const closeModal = vi.fn();
    render(
      <>
        <OverlayBase open onClose={closeNonModal} ariaLabel="Drawer" modal={false} scrim={false} portal={false}>
          <button type="button">in drawer</button>
        </OverlayBase>
        <OverlayBase open onClose={closeModal} ariaLabel="Modal">
          <button type="button">in modal</button>
        </OverlayBase>
      </>,
    );

    await user.keyboard("{Escape}");
    expect(closeModal).toHaveBeenCalledTimes(1);
    expect(closeNonModal).not.toHaveBeenCalled();
  });

  it("closes on a scrim click but not on a click inside the panel", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <OverlayBase open onClose={onClose} ariaLabel="Example">
        <button type="button">inside</button>
      </OverlayBase>,
    );
    await user.click(screen.getByRole("button", { name: "inside" }));
    expect(onClose).not.toHaveBeenCalled();
    await user.click(screen.getByRole("presentation"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("focuses the first focusable descendant by default", () => {
    render(
      <OverlayBase open onClose={() => {}} ariaLabel="Example">
        <button type="button">first</button>
        <button type="button">second</button>
      </OverlayBase>,
    );
    expect(screen.getByRole("button", { name: "first" })).toHaveFocus();
  });

  it("focuses the panel itself when the caller asks for it", () => {
    render(
      <OverlayBase open onClose={() => {}} ariaLabel="Example" initialFocus="panel">
        <button type="button">first</button>
      </OverlayBase>,
    );
    expect(screen.getByRole("dialog")).toHaveFocus();
  });

  it("honours initialFocusRef over either default", () => {
    function WithRef() {
      const ref = useRef<HTMLButtonElement>(null);
      return (
        <OverlayBase open onClose={() => {}} ariaLabel="Example" initialFocus="panel" initialFocusRef={ref}>
          <button type="button">skip</button>
          <button type="button" ref={ref}>
            focus me
          </button>
        </OverlayBase>
      );
    }
    render(<WithRef />);
    expect(screen.getByRole("button", { name: "focus me" })).toHaveFocus();
  });

  it.each([true, false])("restores focus to the opener on close (modal=%s)", async (modal) => {
    const user = userEvent.setup();
    render(<Harness modal={modal} />);
    const trigger = screen.getByRole("button", { name: "open trigger" });
    await user.click(trigger);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("does not pull focus back to the start when the caller re-renders while open", () => {
    function Rerendering() {
      const [count, setCount] = useState(0);
      function close() {
        setCount(-1);
      }
      return (
        <OverlayBase open onClose={close} ariaLabel="Example">
          <button type="button" onClick={() => setCount(count + 1)}>
            first {count}
          </button>
          <button type="button">second</button>
        </OverlayBase>
      );
    }
    const { rerender } = render(<Rerendering />);
    const second = screen.getByRole("button", { name: "second" });
    second.focus();
    rerender(<Rerendering />);
    expect(second).toHaveFocus();
  });

  it("supports labelledBy as an alternative to ariaLabel", () => {
    render(
      <OverlayBase open onClose={() => {}} labelledBy="overlay-heading">
        <h2 id="overlay-heading">Heading title</h2>
      </OverlayBase>,
    );
    expect(screen.getByRole("dialog", { name: "Heading title" })).toBeInTheDocument();
  });
});

// The scrim's entrance is the one piece of overlay motion, and it exists
// only for viewers who have not asked for less of it: the declaration sits
// inside the no-preference query, so a reduced-motion viewer gets no
// animation at all rather than one that merely runs faster.
describe("overlay entrance motion", () => {
  it("declares the scrim animation only under prefers-reduced-motion: no-preference", () => {
    const noPreference = ui.slice(ui.indexOf("@media (prefers-reduced-motion: no-preference)"));
    expect(ui).toContain("@media (prefers-reduced-motion: no-preference)");
    expect(noPreference).toMatch(/\.ui-overlay-scrim\s*\{[^}]*animation:/);
  });

  it("times the entrance from a --dur token rather than a literal duration", () => {
    const rule = ui.match(/\.ui-overlay-scrim\s*\{[^}]*animation:([^;]+);/);
    expect(rule, ".ui-overlay-scrim has no animation declaration").not.toBeNull();
    expect(rule![1]).toContain("var(--dur-");
    expect(rule![1]).not.toMatch(/\d+m?s/);
  });
});
