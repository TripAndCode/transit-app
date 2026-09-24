import { describe, it, expect, afterEach, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ToastProvider } from "./Toast";
import { useToast } from "./toastContext";

const css = readFileSync(resolve(process.cwd(), "src/components/ui/ui.css"), "utf8");

/** The stylesheet is the contract here: vitest runs with `css: false`, so a
 *  class-driven declaration can never be read back off a rendered node. */
function ruleBody(selector: string): string {
  const at = css.indexOf(`${selector} {`);
  if (at === -1) throw new Error(`selector not found: ${selector}`);
  return css.slice(at, css.indexOf("}", at));
}

/** Longer than the auto-dismiss window, so one advance always crosses it
 *  without the test restating the exact constant. */
const PAST_AUTO_DISMISS_MS = 9_000;

const MESSAGE = "Approved AI access for 3";

function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

afterEach(() => {
  vi.useRealTimers();
});

function Harness({ onUndo }: { onUndo?: () => void }) {
  const toast = useToast();
  return (
    <button
      type="button"
      onClick={() => toast.show(MESSAGE, onUndo ? { action: { label: "Undo", onClick: onUndo } } : undefined)}
    >
      fire
    </button>
  );
}

function renderHarness(onUndo?: () => void) {
  render(
    <ToastProvider>
      <Harness onUndo={onUndo} />
    </ToastProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "fire" }));
}

describe("Toast", () => {
  it("announces the message from a portal on document.body", () => {
    renderHarness();
    const toast = screen.getByRole("status");
    expect(toast).toHaveTextContent(MESSAGE);
    const viewport = toast.parentElement as HTMLElement;
    expect(viewport).toHaveClass("ui-toast-viewport");
    expect(viewport.parentElement).toBe(document.body);
  });

  it("stacks on the shared toast rung rather than a fresh literal", () => {
    expect(ruleBody(".ui-toast-viewport")).toContain("var(--z-toast)");
  });

  it("dismisses itself once the quiet window has passed", () => {
    vi.useFakeTimers();
    renderHarness();
    expect(screen.getByRole("status")).toBeInTheDocument();

    advance(PAST_AUTO_DISMISS_MS);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("holds while the pointer rests on it, and resumes once it leaves", () => {
    vi.useFakeTimers();
    renderHarness();
    const toast = screen.getByRole("status");

    fireEvent.mouseOver(toast);
    advance(PAST_AUTO_DISMISS_MS);
    expect(screen.getByRole("status")).toBeInTheDocument();

    fireEvent.mouseOut(toast);
    advance(PAST_AUTO_DISMISS_MS);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("holds while its action has keyboard focus", () => {
    vi.useFakeTimers();
    renderHarness(vi.fn());
    const action = screen.getByRole("button", { name: "Undo" });

    act(() => {
      action.focus();
    });
    advance(PAST_AUTO_DISMISS_MS);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("runs the action and clears the toast with it", () => {
    const onUndo = vi.fn();
    renderHarness(onUndo);

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(onUndo).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("keeps its entrance animation behind a reduced-motion guard", () => {
    const guardAt = css.indexOf("@media (prefers-reduced-motion: no-preference)");
    expect(guardAt).toBeGreaterThan(-1);
    expect(css.indexOf("animation: ui-toast-in")).toBeGreaterThan(guardAt);
    expect(ruleBody(".ui-toast")).not.toContain("animation");
  });
});
