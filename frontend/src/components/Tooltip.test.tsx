import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, act, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Tooltip } from "./Tooltip";
import { computeTooltipPosition } from "./tooltipPosition";

/** Longer than the component's hover-intent delay, so one advance always
 *  crosses it without the test restating the exact constant. */
const PAST_HOVER_INTENT_MS = 400;

/** The dwell timer is driven with fake timers and raw events rather than
 *  user-event: user-event's own async waits deadlock against faked timers,
 *  and what is under test here is the delay, not the input sequence. */
function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

function hover(element: HTMLElement) {
  fireEvent.mouseOver(element);
}

function unhover(element: HTMLElement) {
  fireEvent.mouseOut(element);
}

afterEach(() => {
  vi.useRealTimers();
});

function renderFitButton() {
  render(
    <Tooltip label="Fit all trips">
      <button type="button">Fit</button>
    </Tooltip>,
  );
  return screen.getByRole("button");
}

describe("Tooltip on hover", () => {
  it("waits out the hover-intent delay before showing", () => {
    vi.useFakeTimers();
    const trigger = renderFitButton();

    hover(trigger);
    expect(screen.queryByRole("tooltip")).toBeNull();

    advance(PAST_HOVER_INTENT_MS);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Fit all trips");
  });

  it("hides again when the pointer leaves", () => {
    vi.useFakeTimers();
    const trigger = renderFitButton();

    hover(trigger);
    advance(PAST_HOVER_INTENT_MS);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    unhover(trigger);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("never shows when the pointer leaves before the delay elapses", () => {
    vi.useFakeTimers();
    const trigger = renderFitButton();

    hover(trigger);
    unhover(trigger);
    advance(PAST_HOVER_INTENT_MS);

    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("describes the trigger only while the tooltip is on screen", () => {
    vi.useFakeTimers();
    const trigger = renderFitButton();
    expect(trigger).not.toHaveAttribute("aria-describedby");

    hover(trigger);
    advance(PAST_HOVER_INTENT_MS);
    expect(trigger.getAttribute("aria-describedby")).toBe(screen.getByRole("tooltip").id);

    unhover(trigger);
    expect(trigger).not.toHaveAttribute("aria-describedby");
  });
});

describe("Tooltip on keyboard", () => {
  it("shows immediately on focus, and hides on blur", async () => {
    const user = userEvent.setup();
    render(
      <>
        <Tooltip label="Hide the legend">
          <button type="button">Hide</button>
        </Tooltip>
        <button type="button">Elsewhere</button>
      </>,
    );

    await user.tab();
    expect(screen.getByRole("button", { name: "Hide" })).toHaveFocus();
    expect(screen.getByRole("tooltip")).toHaveTextContent("Hide the legend");

    await user.tab();
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("dismisses on Escape while the trigger keeps focus", async () => {
    const user = userEvent.setup();
    render(
      <Tooltip label="Hide the legend">
        <button type="button">Hide</button>
      </Tooltip>,
    );

    await user.tab();
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(screen.getByRole("button")).toHaveFocus();
  });
});

describe("Tooltip and its trigger", () => {
  it("keeps the trigger's own handlers working", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    const onMouseEnter = vi.fn();
    render(
      <Tooltip label="Fit all trips">
        <button type="button" onClick={onClick} onMouseEnter={onMouseEnter}>
          Fit
        </button>
      </Tooltip>,
    );

    await user.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(onMouseEnter).toHaveBeenCalledTimes(1);
  });
});

describe("computeTooltipPosition", () => {
  const viewport = { width: 1000, height: 800 };
  const tip = { width: 120, height: 40 };
  const trigger = { top: 300, bottom: 320, left: 400, right: 460, width: 60, height: 20 };

  it("centres a top-placed tooltip above the trigger", () => {
    const pos = computeTooltipPosition(trigger, tip, "top", viewport);
    expect(pos.placement).toBe("top");
    expect(pos.left).toBe(400 + 30 - 60);
    expect(pos.top).toBe(300 - 8 - 40);
  });

  it("flips top to bottom when the tooltip would clear the top edge", () => {
    const nearTop = { ...trigger, top: 10, bottom: 30 };
    const pos = computeTooltipPosition(nearTop, tip, "top", viewport);
    expect(pos.placement).toBe("bottom");
    expect(pos.top).toBe(30 + 8);
  });

  it("flips right to left when the tooltip would clear the right edge", () => {
    const nearRight = { ...trigger, left: 930, right: 990 };
    const pos = computeTooltipPosition(nearRight, tip, "right", viewport);
    expect(pos.placement).toBe("left");
    expect(pos.left).toBe(930 - 8 - 120);
  });

  it("keeps the requested side when neither side fits", () => {
    const tall = { width: 120, height: 900 };
    const pos = computeTooltipPosition(trigger, tall, "top", viewport);
    expect(pos.placement).toBe("top");
  });

  it("clamps a vertically-placed tooltip inside the viewport's left edge", () => {
    const nearLeft = { ...trigger, left: 0, right: 60 };
    const pos = computeTooltipPosition(nearLeft, tip, "bottom", viewport);
    expect(pos.left).toBe(8);
  });

  it("clamps a horizontally-placed tooltip inside the viewport's bottom edge", () => {
    const nearBottom = { ...trigger, top: 780, bottom: 800 };
    const pos = computeTooltipPosition(nearBottom, tip, "right", viewport);
    expect(pos.top).toBe(800 - 40 - 8);
  });
});
