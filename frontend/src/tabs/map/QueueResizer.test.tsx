import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { QueueResizer } from "./QueueResizer";
import { DEFAULT_QUEUE_WIDTH, MAX_QUEUE_WIDTH, MIN_QUEUE_WIDTH } from "./queueWidth";

const renderResizer = (width = 300) => {
  const onWidth = vi.fn();
  render(<QueueResizer width={width} label="パネルの幅を変更" onWidth={onWidth} />);
  return { onWidth, handle: screen.getByRole("separator") };
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
    const { onWidth, handle } = renderResizer(300);
    handle.focus();
    await userEvent.keyboard("{ArrowLeft}");
    expect(onWidth).toHaveBeenLastCalledWith(316);
    await userEvent.keyboard("{ArrowRight}");
    expect(onWidth).toHaveBeenLastCalledWith(284);
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
    const { onWidth, handle } = renderResizer(640);
    await userEvent.dblClick(handle);
    expect(onWidth).toHaveBeenLastCalledWith(DEFAULT_QUEUE_WIDTH);
  });
});
