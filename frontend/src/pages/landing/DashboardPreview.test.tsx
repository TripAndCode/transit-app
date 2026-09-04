import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { DashboardPreview } from "./DashboardPreview";

// Mirrors DashboardPreview.tsx's own constants -- kept in sync there rather
// than imported, since the component doesn't export them (they're an
// internal implementation detail, not part of its public API).
const AUTO_ADVANCE_INTERVAL_MS = 4500;
const AUTO_ADVANCE_RESUME_DELAY_MS = 6000;

function mockMatchMedia(matches: boolean) {
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches,
    media: "(prefers-reduced-motion: reduce)",
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  } as unknown as MediaQueryList);
}

/** The flex row holding the sidebar and content column -- the element
 *  `DashboardPreview` attaches its hover/click/keydown listeners to. Found
 *  via a stable, always-rendered landmark (`<nav>`) rather than styling, so
 *  the query survives unrelated markup/style changes. */
function getPreviewShell(): HTMLElement {
  const nav = screen.getByRole("navigation", { name: "See what's inside" });
  const shell = nav.closest("div");
  if (!shell) throw new Error("expected DashboardPreview's shell <div> ancestor");
  return shell;
}

void i18n.changeLanguage("en");

function renderPreview() {
  return render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter>
        <DashboardPreview />
      </MemoryRouter>
    </I18nextProvider>,
  );
}

const NAV_LABELS = ["Overview", "Map", "Analysis", "Agencies", "Latest observations"];

describe("DashboardPreview", () => {
  beforeEach(() => {
    localStorage.removeItem("transit.sidebarCollapsed");
  });
  afterEach(() => {
    localStorage.removeItem("transit.sidebarCollapsed");
  });

  it("shows only the real 5 sidebar tabs as peer nav items, with Ask and Help visually distinct", () => {
    renderPreview();
    for (const label of NAV_LABELS) {
      expect(screen.getByRole("button", { name: new RegExp(label) })).toBeTruthy();
    }
    // Ask exists, but as the dashed-border CTA (not a 6th peer nav item) --
    // it's outside the <nav>, in the sidebar's footer area.
    const nav = screen.getByRole("navigation", { name: "See what's inside" });
    expect(within(nav).queryByRole("button", { name: /^Ask$/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Ask" })).toBeTruthy();

    // Help is a dismissable hint banner with a real link to /help, not a
    // nav button at all.
    const helpLink = screen.getByRole("link", { name: "View Help" });
    expect(helpLink).toHaveAttribute("href", "/help");
    expect(within(nav).queryByText("View Help")).toBeNull();
  });

  it("collapses and expands the sidebar via the real, shared transit.sidebarCollapsed preference", async () => {
    const user = userEvent.setup();
    renderPreview();
    expect(screen.getByText("What's happening right now")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(localStorage.getItem("transit.sidebarCollapsed")).toBe("1");
    // Collapsed: subtitle text is no longer rendered, only the icon-only button remains.
    expect(screen.queryByText("What's happening right now")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Expand sidebar" }));
    expect(localStorage.getItem("transit.sidebarCollapsed")).toBe("0");
    expect(screen.getByText("What's happening right now")).toBeTruthy();
  });

  it("swaps the main panel when a nav tab is selected", async () => {
    const user = userEvent.setup();
    renderPreview();

    await user.click(screen.getByRole("button", { name: /^Analysis/ }));
    expect(screen.getByRole("button", { name: "Historical trend" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /^Agencies/ }));
    expect(screen.getByText("Avg delay (min)")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /^Latest observations/ }));
    expect(screen.getByRole("button", { name: "Latest" })).toBeTruthy();
  });

  it("renders the Map tab full-bleed with floating style/heatmap/legend controls, all functional", async () => {
    const user = userEvent.setup();
    renderPreview();
    await user.click(screen.getByRole("button", { name: /^Map/ }));

    expect(screen.getByText("On-time route")).toBeTruthy();
    expect(screen.getByText("Delayed route")).toBeTruthy();

    const mutedButton = screen.getByRole("button", { name: "Muted" });
    const canvas = document.querySelector("canvas") as HTMLCanvasElement;
    expect(canvas.style.filter).toBe("none");
    await user.click(mutedButton);
    expect(mutedButton).toHaveAttribute("aria-pressed", "true");
    expect(canvas.style.filter).not.toBe("none");

    const heatmapToggle = screen.getByRole("button", { name: "Avg delay" });
    await user.click(heatmapToggle);
    expect(screen.getByRole("button", { name: "90th percentile" })).toBeTruthy();
  });

  it("filters the Overview route list when a filter chip is clicked", async () => {
    const user = userEvent.setup();
    renderPreview();
    expect(screen.getByText("Route R1")).toBeTruthy();
    expect(screen.getByText("Route R7")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Delayed" }));
    expect(screen.queryByText("Route R1")).toBeNull();
    expect(screen.getByText("Route R7")).toBeTruthy();
  });

  it("swaps Analysis figures when the trend/hour toggle changes", async () => {
    const user = userEvent.setup();
    renderPreview();
    await user.click(screen.getByRole("button", { name: /^Analysis/ }));
    expect(screen.getByText("Mon")).toBeTruthy();
    expect(screen.queryByText("18:00")).toBeNull();

    await user.click(screen.getByRole("button", { name: "By hour" }));
    expect(screen.queryByText("Mon")).toBeNull();
    expect(screen.getByText("18:00")).toBeTruthy();
  });

  it("moves the YOU badge and the Overview stats when a different agency is selected in Agencies", async () => {
    const user = userEvent.setup();
    renderPreview();
    await user.click(screen.getByRole("button", { name: /^Agencies/ }));
    await user.click(screen.getByRole("button", { name: /Harborline/ }));

    await user.click(screen.getByRole("button", { name: /^Overview/ }));
    expect(screen.getByText("Route H2")).toBeTruthy();
    expect(screen.queryByText("Route R1")).toBeNull();
  });

  it("drives a real conversation from the Ask CTA's suggestion chips and free-text input", async () => {
    const user = userEvent.setup();
    renderPreview();
    await user.click(screen.getByRole("button", { name: "Ask" }));

    await user.click(screen.getByRole("button", { name: "Which route is running late right now?" }));
    expect(screen.getByText("Route H9 is currently averaging a 4.4 min delay, the most of any route today.")).toBeTruthy();

    const input = screen.getByLabelText("Ask a question");
    await user.type(input, "How about tomorrow?");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(screen.getByText("How about tomorrow?")).toBeTruthy();
    expect(input).toHaveValue("");
  });
});

describe("DashboardPreview auto-advance", () => {
  beforeEach(() => {
    localStorage.removeItem("transit.sidebarCollapsed");
    vi.useFakeTimers();
  });
  afterEach(() => {
    localStorage.removeItem("transit.sidebarCollapsed");
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("cycles Overview -> Map -> Analysis -> Agencies -> Live on its own when left untouched", () => {
    mockMatchMedia(false);
    renderPreview();
    expect(screen.getByText("Route R1")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(AUTO_ADVANCE_INTERVAL_MS);
    });
    expect(screen.getByText("On-time route")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(AUTO_ADVANCE_INTERVAL_MS);
    });
    expect(screen.getByRole("button", { name: "Historical trend" })).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(AUTO_ADVANCE_INTERVAL_MS);
    });
    expect(screen.getByText("Avg delay (min)")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(AUTO_ADVANCE_INTERVAL_MS);
    });
    expect(screen.getByRole("button", { name: "Latest" })).toBeTruthy();
  });

  it("pauses while the pointer hovers the preview, and resumes once it leaves", () => {
    mockMatchMedia(false);
    renderPreview();
    const shell = getPreviewShell();

    fireEvent.mouseEnter(shell);
    act(() => {
      vi.advanceTimersByTime(AUTO_ADVANCE_INTERVAL_MS * 3);
    });
    expect(screen.getByText("Route R1")).toBeTruthy();

    fireEvent.mouseLeave(shell);
    act(() => {
      vi.advanceTimersByTime(AUTO_ADVANCE_INTERVAL_MS);
    });
    expect(screen.getByText("On-time route")).toBeTruthy();
  });

  it("pauses after a click interaction and resumes once the grace delay elapses", () => {
    mockMatchMedia(false);
    renderPreview();
    const shell = getPreviewShell();

    fireEvent.click(within(shell).getByRole("button", { name: "Collapse sidebar" }));

    act(() => {
      vi.advanceTimersByTime(AUTO_ADVANCE_INTERVAL_MS);
    });
    // Still Overview -- the tick right after the click falls inside the
    // resume-delay grace period, not just inside the auto-advance interval.
    expect(screen.getByText("Route R1")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(AUTO_ADVANCE_RESUME_DELAY_MS);
    });
    expect(screen.getByText("On-time route")).toBeTruthy();
  });

  it("pauses after a keyboard interaction the same way it does for a click", () => {
    mockMatchMedia(false);
    renderPreview();
    const shell = getPreviewShell();

    fireEvent.keyDown(within(shell).getByRole("button", { name: "Collapse sidebar" }), { key: "Tab" });

    act(() => {
      vi.advanceTimersByTime(AUTO_ADVANCE_INTERVAL_MS);
    });
    expect(screen.getByText("Route R1")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(AUTO_ADVANCE_RESUME_DELAY_MS);
    });
    expect(screen.getByText("On-time route")).toBeTruthy();
  });

  it("never auto-advances when the visitor prefers reduced motion", () => {
    mockMatchMedia(true);
    renderPreview();

    act(() => {
      vi.advanceTimersByTime(AUTO_ADVANCE_INTERVAL_MS * 5);
    });
    expect(screen.getByText("Route R1")).toBeTruthy();
  });

  it("resumes the cycle from the top after the visitor opens the Ask CTA and leaves it idle", () => {
    mockMatchMedia(false);
    renderPreview();
    const shell = getPreviewShell();

    fireEvent.click(within(shell).getByRole("button", { name: "Ask" }));
    expect(screen.getByLabelText("Ask a question")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(AUTO_ADVANCE_INTERVAL_MS + AUTO_ADVANCE_RESUME_DELAY_MS);
    });
    expect(screen.getByText("Route R1")).toBeTruthy();
  });
});
