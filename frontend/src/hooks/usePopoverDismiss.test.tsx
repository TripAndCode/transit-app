import { useRef, useState } from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { usePopoverDismiss } from "./usePopoverDismiss";
import { useFocusTrap } from "./useFocusTrap";

function Popover({ onClose }: { onClose: () => void }) {
  const [open, setOpen] = useState(true);
  const rootRef = useRef<HTMLDivElement>(null);
  usePopoverDismiss(open, rootRef, () => {
    setOpen(false);
    onClose();
  });
  return (
    <div>
      <button type="button">outside</button>
      {open && (
        <div ref={rootRef} data-testid="popover">
          <button type="button">inside</button>
        </div>
      )}
    </div>
  );
}

function Trap({ onEscape }: { onEscape: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(true, ref, onEscape);
  return (
    <div ref={ref} tabIndex={-1}>
      <button type="button">trap</button>
    </div>
  );
}

describe("usePopoverDismiss", () => {
  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(<Popover onClose={onClose} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("popover")).toBeNull();
  });

  it("closes on a pointer-down outside its root and ignores one inside", () => {
    const onClose = vi.fn();
    render(<Popover onClose={onClose} />);
    fireEvent.mouseDown(screen.getByText("inside"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(screen.getByText("outside"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("leaves Escape to a focus trap opened over it", () => {
    const onClose = vi.fn();
    const onEscape = vi.fn();
    render(
      <>
        <Popover onClose={onClose} />
        <Trap onEscape={onEscape} />
      </>,
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onEscape).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByTestId("popover")).toBeInTheDocument();
  });

  it("answers Escape alone when opened inside a sheet that traps focus", () => {
    const onClose = vi.fn();
    const onSheetEscape = vi.fn();
    function SheetWithMenu() {
      const sheetRef = useRef<HTMLDivElement>(null);
      const [menuOpen, setMenuOpen] = useState(false);
      useFocusTrap(true, sheetRef, onSheetEscape);
      return (
        <div ref={sheetRef} tabIndex={-1}>
          <button type="button" onClick={() => setMenuOpen(true)}>
            open menu
          </button>
          {menuOpen && <Popover onClose={onClose} />}
        </div>
      );
    }
    render(<SheetWithMenu />);
    fireEvent.click(screen.getByText("open menu"));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSheetEscape).not.toHaveBeenCalled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onSheetEscape).toHaveBeenCalledTimes(1);
  });

  it("listens for nothing while closed", () => {
    const onClose = vi.fn();
    function Closed() {
      const rootRef = useRef<HTMLDivElement>(null);
      usePopoverDismiss(false, rootRef, onClose);
      return <div ref={rootRef} />;
    }
    render(<Closed />);
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.mouseDown(document.body);
    expect(onClose).not.toHaveBeenCalled();
  });
});
