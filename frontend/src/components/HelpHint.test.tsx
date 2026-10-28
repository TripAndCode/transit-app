import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, act, fireEvent } from "@testing-library/react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { HelpHint } from "./HelpHint";
import { blockStorage } from "../test/blockStorage";

function renderHint(path = "/") {
  return renderWithProviders(
    <MemoryRouter initialEntries={[path]}>
      <HelpHint />
    </MemoryRouter>,
  );
}

/** The hint stays mounted across navigation, as it does under App. */
function NavigatingHint() {
  const navigate = useNavigate();
  return (
    <>
      <HelpHint />
      <button type="button" onClick={() => navigate("/help")}>
        to help
      </button>
      <button type="button" onClick={() => navigate("/agencies/1/pulse")}>
        to pulse
      </button>
    </>
  );
}

function stubPhoneViewport() {
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches: true,
    media: "(max-width: 640px)",
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  } as unknown as MediaQueryList);
}

describe("HelpHint", () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
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

  it("stays off the help page itself", () => {
    vi.useFakeTimers();
    renderHint("/help");
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("retires for good once the visitor opens help by any route, not only its own link", () => {
    vi.useFakeTimers();
    const { unmount } = renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/1/pulse"]}>
        <NavigatingHint />
      </MemoryRouter>,
    );
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(screen.getByRole("status")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "to help" }));
    expect(screen.queryByRole("status")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "to pulse" }));
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(screen.queryByRole("status")).toBeNull();
    unmount();

    renderHint("/agencies/1/pulse");
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("sits in the right-hand corner, clear of the rail's account menu and the toasts", () => {
    vi.useFakeTimers();
    renderHint("/agencies/1/pulse");
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    const hint = screen.getByRole("status");
    expect(hint.style.right).toBe("16px");
    expect(hint.style.left).toBe("");
    expect(hint.style.bottom).toBe("16px");
  });

  it("on a phone, sits above the bottom tab bar rather than on it", () => {
    stubPhoneViewport();
    vi.useFakeTimers();
    renderHint("/agencies/1/pulse");
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(screen.getByRole("status").style.bottom).toBe("calc(72px + env(safe-area-inset-bottom))");
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
