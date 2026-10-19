import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, act, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { HelpHint } from "./HelpHint";
import { blockStorage } from "../test/blockStorage";

function renderHint() {
  renderWithProviders(
    <MemoryRouter>
      <HelpHint />
    </MemoryRouter>,
  );
}

describe("HelpHint", () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not show immediately on a fresh visit", () => {
    vi.useFakeTimers();
    renderHint();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows after the show-delay on a fresh visit", () => {
    vi.useFakeTimers();
    renderHint();
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByText("New here? Check out the User Manual for a walkthrough.")).toBeTruthy();
  });

  it("dismissing hides it and it stays hidden across a remount", () => {
    vi.useFakeTimers();
    const { unmount } = renderWithProviders(
      <MemoryRouter>
        <HelpHint />
      </MemoryRouter>,
    );
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    fireEvent.click(screen.getByRole("button", { name: "Dismiss help hint" }));
    expect(screen.queryByRole("status")).toBeNull();
    unmount();

    renderHint();
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("never shows once the first-visit window has already passed (e.g. an old localStorage value)", () => {
    localStorage.setItem("help_hint_first_visit_at", String(Date.now() - 10 * 60 * 1000));
    vi.useFakeTimers();
    renderHint();
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(screen.queryByRole("status")).toBeNull();
  });

  // One test, because blocked storage keeps the flags in module memory for the
  // page's life, which a second test in this file would inherit.
  it("offers the hint where site data is blocked, and keeps its dismissal through a remount", () => {
    blockStorage("localStorage");
    vi.useFakeTimers();
    const first = renderWithProviders(
      <MemoryRouter>
        <HelpHint />
      </MemoryRouter>,
    );
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    fireEvent.click(screen.getByRole("button", { name: "Dismiss help hint" }));
    expect(screen.queryByRole("status")).toBeNull();
    first.unmount();
    renderHint();
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.queryByRole("status")).toBeNull();
  });
});
