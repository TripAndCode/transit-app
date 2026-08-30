import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, act, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { GuestPrompt } from "./GuestPrompt";

// Mirrors GuestPrompt.tsx's own private constants (not exported).
const STARTED_KEY = "guest_started_at";
const NUDGE_AFTER_MS = 10 * 60 * 1000;

const useSessionMock = vi.fn();
vi.mock("../api/auth", () => ({
  useSession: () => useSessionMock(),
}));

function renderPrompt() {
  renderWithProviders(
    <MemoryRouter>
      <GuestPrompt />
    </MemoryRouter>,
  );
}

/** Seed localStorage so the 10-minute nudge threshold has already elapsed. */
function seedElapsedStart() {
  localStorage.setItem(STARTED_KEY, String(Date.now() - NUDGE_AFTER_MS - 1_000));
}

// Mirrors Sidebar.test.tsx's mockMatchMedia helper — same shared
// max-width:640px query used by useTapToExpandBanner.
function mockMatchMedia(matches: boolean) {
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches,
    media: "(max-width: 640px)",
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  } as unknown as MediaQueryList);
}

describe("GuestPrompt", () => {
  beforeEach(() => {
    localStorage.clear();
    useSessionMock.mockReturnValue({ data: null, isLoading: false });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("does not show for a logged-in user", () => {
    useSessionMock.mockReturnValue({ data: { user_id: 1 }, isLoading: false });
    seedElapsedStart();
    vi.useFakeTimers();
    renderPrompt();
    act(() => {
      vi.advanceTimersByTime(0);
    });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows once the nudge threshold has elapsed for an anonymous user", () => {
    seedElapsedStart();
    vi.useFakeTimers();
    renderPrompt();
    act(() => {
      vi.advanceTimersByTime(0);
    });
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("dismissing hides it", () => {
    seedElapsedStart();
    vi.useFakeTimers();
    renderPrompt();
    act(() => {
      vi.advanceTimersByTime(0);
    });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("renders the message as a single tappable line on a narrow viewport", () => {
    mockMatchMedia(true);
    seedElapsedStart();
    vi.useFakeTimers();
    renderPrompt();
    act(() => {
      vi.advanceTimersByTime(0);
    });
    const buttons = screen.getAllByRole("button");
    const messageButton = buttons.find((b) => b.getAttribute("aria-expanded") === "false");
    expect(messageButton).toBeDefined();
  });
});
