import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { QueueResizer } from "./QueueResizer";
import { DEFAULT_QUEUE_WIDTH, MAX_QUEUE_WIDTH, MIN_QUEUE_WIDTH } from "./queueWidth";

const renderResizer = (width = 300) => {
  const onWidth = vi.fn();
  const onCommit = vi.fn();
  render(<QueueResizer width={width} label="パネルの幅を変更" onWidth={onWidth} onCommit={onCommit} />);
  return { onWidth, onCommit, handle: screen.getByRole("separator") };
};

// jsdom lacks a PointerEvent constructor, so fireEvent.pointerDown can't carry
// clientX. PointerEvent is a MouseEvent subtype, so a same-named MouseEvent
// exercises the same onPointer* handlers with a real clientX.
const firePointer = (
  target: Element,
  type: "pointerdown" | "pointermove" | "pointerup",
  clientX: number,
) => {
  const event = new MouseEvent(type, { clientX, bubbles: true, cancelable: true });
  Object.defineProperty(event, "pointerId", { value: 1 });
  fireEvent(target, event);
};

describe("QueueResizer", () => {
  it("exposes the current width to assistive tech", () => {
    const { handle } = renderResizer(300);
    expect(handle).toHaveAttribute("aria-valuenow", "300");
    expect(handle).toHaveAttribute("aria-valuemin", String(MIN_QUEUE_WIDTH));
    expect(handle).toHaveAttribute("aria-valuemax", String(MAX_QUEUE_WIDTH));
    expect(handle).toHaveAccessibleName("パネルの幅を変更");
  });

  it("widens the right-hand panel on ArrowLeft and narrows it on ArrowRight", async () => {
    const { onWidth, onCommit, handle } = renderResizer(300);
    handle.focus();
    await userEvent.keyboard("{ArrowLeft}");
    expect(onWidth).toHaveBeenLastCalledWith(316);
    expect(onCommit).toHaveBeenLastCalledWith(316);
    await userEvent.keyboard("{ArrowRight}");
    expect(onWidth).toHaveBeenLastCalledWith(284);
    expect(onCommit).toHaveBeenLastCalledWith(284);
  });

  it("takes a bigger step while shift is held", async () => {
    const { onWidth, handle } = renderResizer(300);
    handle.focus();
    await userEvent.keyboard("{Shift>}{ArrowLeft}{/Shift}");
    expect(onWidth).toHaveBeenLastCalledWith(348);
  });

  it("clamps keyboard resizing at the bounds", async () => {
    const { onWidth, handle } = renderResizer(MAX_QUEUE_WIDTH);
    handle.focus();
    await userEvent.keyboard("{ArrowLeft}");
    expect(onWidth).toHaveBeenLastCalledWith(MAX_QUEUE_WIDTH);
  });

  it("restores the default width on double click", async () => {
    const { onWidth, onCommit, handle } = renderResizer(640);
    await userEvent.dblClick(handle);
    expect(onWidth).toHaveBeenLastCalledWith(DEFAULT_QUEUE_WIDTH);
    expect(onCommit).toHaveBeenLastCalledWith(DEFAULT_QUEUE_WIDTH);
  });

  it("widens on a leftward pointer drag and narrows on a rightward one", () => {
    const { onWidth, handle } = renderResizer(300);
    firePointer(handle, "pointerdown", 300);
    firePointer(handle, "pointermove", 280);
    expect(onWidth).toHaveBeenLastCalledWith(320);
    firePointer(handle, "pointermove", 340);
    expect(onWidth).toHaveBeenLastCalledWith(260);
  });

  it("clamps pointer-drag resizing at the bounds", () => {
    const { onWidth, handle } = renderResizer(MIN_QUEUE_WIDTH);
    firePointer(handle, "pointerdown", 300);
    firePointer(handle, "pointermove", 1000);
    expect(onWidth).toHaveBeenLastCalledWith(MIN_QUEUE_WIDTH);
  });

  it("does not commit on every move, only once the pointer is released", () => {
    const { onWidth, onCommit, handle } = renderResizer(300);
    firePointer(handle, "pointerdown", 300);
    firePointer(handle, "pointermove", 280);
    expect(onCommit).not.toHaveBeenCalled();
    firePointer(handle, "pointerup", 280);
    expect(onCommit).toHaveBeenCalledTimes(1);
    expect(onCommit).toHaveBeenLastCalledWith(320);
    onWidth.mockClear();
    onCommit.mockClear();
    firePointer(handle, "pointermove", 200);
    expect(onWidth).not.toHaveBeenCalled();
    expect(onCommit).not.toHaveBeenCalled();
  });
});
