import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { useState } from "react";
import { FirstRunTour } from "./FirstRunTour";
import { DEFAULT_WINDOW } from "../api/onTimeDefault";
import { readTourSeen, resetTourSeenMemoryForTests } from "../api/tourSeen";

void i18n.changeLanguage("en");

/** The tour as Pulse owns it, holding its step. */
function Tour({ onTime = DEFAULT_WINDOW }: { onTime?: typeof DEFAULT_WINDOW }) {
  const [step, setStep] = useState(0);
  return <FirstRunTour onTime={onTime} step={step} onStep={setStep} onLater={() => {}} />;
}

/** The three anchors FirstRunTour looks for, standing in for Pulse's
 *  headline figure, its on-time pointer and its scope sentence. */
function renderTourWithAnchors(onTime = DEFAULT_WINDOW) {
  return render(
    <I18nextProvider i18n={i18n}>
      <div>
        <div data-tour="pulse-delay">2.4 min</div>
        <p data-tour="pulse-on-time">On-time % is on Routes</p>
        <div data-tour="pulse-scope">Viewing all routes</div>
        <Tour onTime={onTime} />
      </div>
    </I18nextProvider>,
  );
}

/** The tour with no anchor on the page yet. */
function renderTourAlone() {
  return render(
    <I18nextProvider i18n={i18n}>
      <Tour />
    </I18nextProvider>,
  );
}

describe("FirstRunTour", () => {
  beforeEach(() => {
    localStorage.clear();
    resetTourSeenMemoryForTests();
  });

  // One test makes storage throw; without this every later test inherits it
  // and sees an "unavailable" store, which now suppresses the tour.
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not render once the tour has already been seen", () => {
    localStorage.setItem("transit.pulseTourSeen", "1");
    renderTourWithAnchors();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens on what one delay is, before any control", () => {
    renderTourWithAnchors();
    const dialog = screen.getByRole("dialog");
    expect(dialog).not.toHaveAttribute("hidden");
    expect(within(dialog).getByText("What a delay is here")).toBeTruthy();
    expect(within(dialog).getByText(/early departure counts as a negative delay/)).toBeTruthy();
    expect(within(dialog).getByText("Step 1 of 3")).toBeTruthy();
  });

  it("states the on-time window it is given, early edge included", async () => {
    const user = userEvent.setup();
    renderTourWithAnchors({ early_sec: 60, late_sec: 300 });
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText(/between 1 min early and 5 min late\. Leaving earlier than that is not on time/)).toBeTruthy();
  });

  it("says every early departure is on time when the window has no early edge", async () => {
    const user = userEvent.setup();
    renderTourWithAnchors({ early_sec: null, late_sec: 60 });
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText(/no more than 1 min late, and every early departure counts as on time/)).toBeTruthy();
  });

  it("uses no timetable-type jargon in any step", async () => {
    const user = userEvent.setup();
    renderTourWithAnchors();
    const bodies: string[] = [];
    for (let i = 0; i < 3; i++) {
      bodies.push(screen.getByRole("dialog").textContent ?? "");
      if (i < 2) await user.click(screen.getByRole("button", { name: "Next" }));
    }
    expect(bodies.join(" ")).not.toMatch(/service pattern|timetable type/i);
    expect([i18n.t("tour.delay.body", { lng: "ja" }), i18n.t("tour.scope.body", { lng: "ja" })].join("")).not.toContain("種別");
  });

  it("advances through all three steps and persists on the final 'Got it'", async () => {
    const user = userEvent.setup();
    renderTourWithAnchors();

    expect(screen.getByText("What a delay is here")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("What on time means")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("What this screen covers")).toBeTruthy();

    expect(readTourSeen()).toBe("unseen");
    await user.click(screen.getByRole("button", { name: "Got it" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(readTourSeen()).toBe("seen");
  });

  it("'Later' closes the tour for this mount without persisting it as seen", async () => {
    const user = userEvent.setup();
    renderTourWithAnchors();
    await user.click(screen.getByRole("button", { name: "Later" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(readTourSeen()).toBe("unseen");
  });

  it("the dismiss (x) control persists the tour as seen", async () => {
    const user = userEvent.setup();
    renderTourWithAnchors();
    await user.click(screen.getByRole("button", { name: "Dismiss this tour" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(readTourSeen()).toBe("seen");
  });

  // role="dialog" with no focus move means a keyboard user gets no cue it
  // appeared and must tab the whole app shell to reach it -- the panel is
  // portalled to the end of <body>.
  it("moves focus into the panel and hands it back when dismissed", async () => {
    const outside = document.createElement("button");
    outside.textContent = "outside";
    document.body.appendChild(outside);
    outside.focus();
    expect(document.activeElement).toBe(outside);

    renderTourWithAnchors();
    await screen.findByRole("dialog");
    const panel = screen.getByRole("dialog");
    expect(panel.contains(document.activeElement)).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: i18n.t("tour.later") }));
    expect(document.activeElement).toBe(outside);
    outside.remove();
  });

  it("closes on Escape the same way the x control does, and hands focus back", async () => {
    const outside = document.createElement("button");
    outside.textContent = "outside";
    document.body.appendChild(outside);
    outside.focus();

    renderTourWithAnchors();
    await screen.findByRole("dialog");

    await userEvent.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(readTourSeen()).toBe("seen");
    expect(document.activeElement).toBe(outside);
    outside.remove();
  });

  it("keeps Tab inside the tour card", async () => {
    // The card is portalled to the end of <body>: without a trap, Tab out of
    // its last control lands on whatever the app shell renders first, with
    // the tour still on screen.
    const user = userEvent.setup();
    renderTourWithAnchors();
    const dialog = await screen.findByRole("dialog");

    within(dialog).getByRole("button", { name: "Next" }).focus();
    await user.tab();
    expect(within(dialog).getByRole("button", { name: "Dismiss this tour" })).toHaveFocus();

    await user.tab({ shift: true });
    expect(within(dialog).getByRole("button", { name: "Next" })).toHaveFocus();
  });

  it("stays away when the store cannot remember a dismissal", () => {
    // Otherwise the tour reappears on every single mount, forever.
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    renderTourWithAnchors();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  // The anchor arriving late is what the retry poll is for, so it is also
  // the case the focus cue has to survive -- the panel appears on screen
  // several ticks after mount.
  it("moves focus when the anchor only appears after mounting", async () => {
    vi.useFakeTimers();
    try {
      const { container } = renderTourAlone();
      const panel = document.querySelector<HTMLElement>(".first-run-tour");
      expect(panel?.hidden).toBe(true);

      const anchor = document.createElement("div");
      anchor.setAttribute("data-tour", "pulse-delay");
      container.appendChild(anchor);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(600);
      });

      expect(panel?.hidden).toBe(false);
      expect(panel?.contains(document.activeElement)).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  // A 250 ms poll that keeps forcing layout for the whole life of the tour
  // is work nothing consumes: once the anchor is found, resize, scroll and
  // the anchor's own ResizeObserver cover every way it can move.
  it("starts no retry poll when the anchor is already on the page", () => {
    const setInterval = vi.spyOn(window, "setInterval");
    renderTourWithAnchors();
    expect(setInterval.mock.calls.filter(([, delay]) => delay === 250)).toHaveLength(0);
  });

  it("stops the retry poll as soon as a late anchor is found", async () => {
    vi.useFakeTimers();
    try {
      const setInterval = vi.spyOn(window, "setInterval");
      const clearInterval = vi.spyOn(window, "clearInterval");
      const { container } = renderTourAlone();
      const pollIndex = setInterval.mock.calls.findIndex(([, delay]) => delay === 250);
      expect(pollIndex).toBeGreaterThanOrEqual(0);
      const intervalId = setInterval.mock.results[pollIndex].value;

      const anchor = document.createElement("div");
      anchor.setAttribute("data-tour", "pulse-delay");
      container.appendChild(anchor);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });

      expect(clearInterval).toHaveBeenCalledWith(intervalId);
      const before = document.querySelectorAll('[data-tour="pulse-delay"]').length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(document.querySelectorAll('[data-tour="pulse-delay"]').length).toBe(before);
      expect(document.querySelector<HTMLElement>(".first-run-tour")?.hidden).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("watches the anchor's own box so a reflow under it moves the panel", () => {
    const observed: Element[] = [];
    const disconnect = vi.fn();
    class FakeResizeObserver {
      constructor(private callback: ResizeObserverCallback) {}
      observe(target: Element) {
        observed.push(target);
      }
      unobserve() {}
      disconnect = disconnect;
      fire() {
        this.callback([], this as unknown as ResizeObserver);
      }
    }
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    try {
      renderTourWithAnchors();
      expect(observed).toEqual([document.querySelector('[data-tour="pulse-delay"]')]);
    } finally {
      vi.unstubAllGlobals();
    }
  });
  it("resumes the search and watches the new node when the anchor unmounts and remounts", async () => {
    vi.useFakeTimers();
    const observed: Element[] = [];
    class FakeResizeObserver {
      observe(target: Element) {
        observed.push(target);
      }
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    try {
      const { container } = renderTourAlone();
      const first = document.createElement("div");
      first.setAttribute("data-tour", "pulse-delay");
      container.appendChild(first);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });
      expect(observed).toEqual([first]);

      first.remove();
      act(() => {
        window.dispatchEvent(new Event("resize"));
      });
      await act(async () => {
        vi.advanceTimersToNextFrame();
      });
      expect(document.querySelector<HTMLElement>(".first-run-tour")?.hidden).toBe(true);

      const second = document.createElement("div");
      second.setAttribute("data-tour", "pulse-delay");
      container.appendChild(second);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });
      expect(observed).toEqual([first, second]);
      expect(document.querySelector<HTMLElement>(".first-run-tour")?.hidden).toBe(false);
    } finally {
      vi.unstubAllGlobals();
      vi.useRealTimers();
    }
  });

  it("coalesces scroll and resize into one frame of repositioning", () => {
    const raf = vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);
    renderTourWithAnchors();
    raf.mockClear();
    for (let i = 0; i < 4; i++) fireEvent.scroll(window);
    fireEvent(window, new Event("resize"));
    expect(raf).toHaveBeenCalledTimes(1);
  });
});
