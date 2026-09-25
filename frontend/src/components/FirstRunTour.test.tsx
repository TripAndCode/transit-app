import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { FirstRunTour } from "./FirstRunTour";
import { readTourSeen, resetTourSeenMemoryForTests } from "../api/tourSeen";

void i18n.changeLanguage("en");

/** The three real anchors FirstRunTour looks for, standing in for
 *  MapTab's FilterDock/OperationsTripPanel and Sidebar's Ask NavLink. */
function renderTourWithAnchors() {
  return render(
    <I18nextProvider i18n={i18n}>
      <div>
        <div data-tour="filter-bar">filter dock</div>
        <div data-tour="map-inspect">observed trips</div>
        <a data-tour="ask-nav" href="/ask">
          Ask
        </a>
        <FirstRunTour />
      </div>
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
    localStorage.setItem("transit.tourSeen", "1");
    renderTourWithAnchors();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows the first step (filters) anchored on the real dashboard", () => {
    renderTourWithAnchors();
    const dialog = screen.getByRole("dialog");
    expect(dialog).not.toHaveAttribute("hidden");
    expect(within(dialog).getByText("Narrow what you're looking at")).toBeTruthy();
    expect(within(dialog).getByText("Step 1 of 3")).toBeTruthy();
  });

  it("advances through all three steps and persists on the final 'Got it'", async () => {
    const user = userEvent.setup();
    renderTourWithAnchors();

    expect(screen.getByText("Narrow what you're looking at")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Inspect what's running now")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Ask a question")).toBeTruthy();

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
      const { container } = render(
        <I18nextProvider i18n={i18n}>
          <FirstRunTour />
        </I18nextProvider>,
      );
      const panel = document.querySelector<HTMLElement>(".first-run-tour");
      expect(panel?.hidden).toBe(true);

      const anchor = document.createElement("div");
      anchor.setAttribute("data-tour", "filter-bar");
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
      const { container } = render(
        <I18nextProvider i18n={i18n}>
          <FirstRunTour />
        </I18nextProvider>,
      );
      const pollIndex = setInterval.mock.calls.findIndex(([, delay]) => delay === 250);
      expect(pollIndex).toBeGreaterThanOrEqual(0);
      const intervalId = setInterval.mock.results[pollIndex].value;

      const anchor = document.createElement("div");
      anchor.setAttribute("data-tour", "filter-bar");
      container.appendChild(anchor);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });

      expect(clearInterval).toHaveBeenCalledWith(intervalId);
      const before = document.querySelectorAll('[data-tour="filter-bar"]').length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(document.querySelectorAll('[data-tour="filter-bar"]').length).toBe(before);
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
      expect(observed).toEqual([document.querySelector('[data-tour="filter-bar"]')]);
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
      const { container } = render(
        <I18nextProvider i18n={i18n}>
          <FirstRunTour />
        </I18nextProvider>,
      );
      const first = document.createElement("div");
      first.setAttribute("data-tour", "filter-bar");
      container.appendChild(first);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });
      expect(observed).toEqual([first]);

      first.remove();
      act(() => {
        window.dispatchEvent(new Event("resize"));
      });
      expect(document.querySelector<HTMLElement>(".first-run-tour")?.hidden).toBe(true);

      const second = document.createElement("div");
      second.setAttribute("data-tour", "filter-bar");
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
});
