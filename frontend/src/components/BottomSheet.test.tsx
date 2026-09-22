import { useState } from "react";
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BottomSheet } from "./BottomSheet";
import { nextSnap, SNAP_HEIGHT_VH, type SnapPoint } from "./bottomSheetSnap";

describe("nextSnap", () => {
  it("settles on the nearest snap point when released with no meaningful velocity", () => {
    expect(nextSnap(0, 0)).toBe("peek");
    expect(nextSnap(0.5, 0)).toBe("half");
    expect(nextSnap(1, 0)).toBe("full");
    expect(nextSnap(0.15, 0)).toBe("peek");
    expect(nextSnap(0.4, 0)).toBe("half");
    expect(nextSnap(0.85, 0)).toBe("full");
  });

  it("advances one snap toward full on a fast upward fling, from wherever it was released", () => {
    // Negative velocity == dragging up (toward the "full" end of the scale).
    expect(nextSnap(0.1, -1)).toBe("half");
    expect(nextSnap(0.6, -1)).toBe("full");
  });

  it("advances one snap toward peek on a fast downward fling", () => {
    expect(nextSnap(0.9, 1)).toBe("half");
    expect(nextSnap(0.4, 1)).toBe("peek");
  });

  it("never flings past the ends of the scale", () => {
    expect(nextSnap(0.95, -1)).toBe("full");
    expect(nextSnap(0.05, 1)).toBe("peek");
  });

  it("clamps an out-of-range position into [0, 1] before choosing a snap", () => {
    expect(nextSnap(-0.4, 0)).toBe("peek");
    expect(nextSnap(1.4, 0)).toBe("full");
  });
});

function Harness({ initial = "peek" as SnapPoint }: { initial?: SnapPoint }) {
  const [snap, setSnap] = useState<SnapPoint>(initial);
  return (
    <div>
      <button type="button">outside trigger</button>
      <BottomSheet snap={snap} onSnapChange={setSnap} ariaLabel="Trips to check">
        <button type="button">first row</button>
        <button type="button">last row</button>
      </BottomSheet>
    </div>
  );
}

function grabHandle(): HTMLElement {
  const handle = screen.getByRole("button", { name: /expand|collapse|handle/i });
  handle.setPointerCapture = () => {};
  return handle;
}

/** jsdom implements no `PointerEvent`, so `fireEvent.pointerDown` falls back
 *  to a bare `Event` that carries no `clientY` -- every coordinate the drag
 *  reads comes back undefined and the resulting NaN height is silently
 *  dropped by CSSOM, leaving the element at its last valid value. A
 *  MouseEvent dispatched under the pointer event's name carries the
 *  coordinates and still reaches React's `onPointerDown`. */
function pointer(el: HTMLElement, type: string, clientY: number): void {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientY });
  Object.defineProperty(event, "pointerId", { value: 1 });
  fireEvent(el, event);
}

describe("BottomSheet", () => {
  it("renders a drag handle and the sheet content at every snap point", () => {
    render(<Harness />);
    expect(screen.getByRole("button", { name: /expand|collapse|handle/i })).toBeTruthy();
    expect(screen.getByText("first row")).toBeTruthy();
  });

  it("advances one snap point when ArrowUp is pressed on the handle", () => {
    render(<Harness initial="peek" />);
    const handle = screen.getByRole("button", { name: /expand|collapse|handle/i });
    expect(handle).toHaveAttribute("aria-expanded", "false");
    fireEvent.keyDown(handle, { key: "ArrowUp" });
    expect(handle).toHaveAttribute("aria-expanded", "true");
  });

  it("is not a dialog and does not trap focus while at peek or half", () => {
    render(<Harness initial="half" />);
    const region = screen.getByLabelText("Trips to check");
    expect(region).not.toHaveAttribute("aria-modal");
  });

  it("becomes a trapping dialog once snapped to full", () => {
    render(<Harness initial="full" />);
    const region = screen.getByLabelText("Trips to check");
    expect(region).toHaveAttribute("aria-modal", "true");
    expect(region).toHaveAttribute("role", "dialog");
  });

  it("traps Tab from the sheet's last focusable descendant back to its handle", async () => {
    const user = userEvent.setup();
    render(<Harness initial="full" />);
    screen.getByText("last row").focus();
    await user.tab();
    expect(screen.getByRole("button", { name: /expand|collapse|handle/i })).toHaveFocus();
  });

  it("holds its resting height when a drag starts but has not travelled", () => {
    render(<Harness initial="peek" />);
    const region = screen.getByLabelText("Trips to check");
    const handle = grabHandle();

    const restingY = 400;
    pointer(handle, "pointerdown", restingY);
    pointer(handle, "pointermove", restingY);

    expect(region.style.height).toBe(`${SNAP_HEIGHT_VH.peek}vh`);
  });

  it("grows the sheet as the handle is dragged up from peek", () => {
    render(<Harness initial="peek" />);
    const region = screen.getByLabelText("Trips to check");
    const handle = grabHandle();

    pointer(handle, "pointerdown", 400);
    pointer(handle, "pointermove", 300);

    expect(parseFloat(region.style.height)).toBeGreaterThan(SNAP_HEIGHT_VH.peek);
  });

  it("collapses from full to half on Escape, moving focus back to the handle", () => {
    render(<Harness initial="full" />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByLabelText("Trips to check")).not.toHaveAttribute("aria-modal");
  });
});
