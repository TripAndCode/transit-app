import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useTapToExpandBanner } from "./useTapToExpandBanner";

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

function Probe() {
  const { messageProps } = useTapToExpandBanner();
  return <span {...messageProps}>Full message text goes here</span>;
}

describe("useTapToExpandBanner", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders full, non-truncated style with no button role on desktop", () => {
    mockMatchMedia(false);
    render(<Probe />);
    const el = screen.getByText("Full message text goes here");
    expect(el).not.toHaveAttribute("role");
    expect(el).toHaveStyle({ flex: "1 1 0%" });
  });

  it("renders a truncated, tappable single line on mobile", () => {
    mockMatchMedia(true);
    render(<Probe />);
    const el = screen.getByRole("button", { name: "Full message text goes here" });
    expect(el).toHaveAttribute("aria-expanded", "false");
    expect(el).toHaveStyle({ whiteSpace: "nowrap", textOverflow: "ellipsis" });
  });

  it("expands to full (non-truncated) style after a tap on mobile", async () => {
    mockMatchMedia(true);
    render(<Probe />);
    await userEvent.click(screen.getByRole("button"));
    const el = screen.getByText("Full message text goes here");
    expect(el).not.toHaveAttribute("role");
    expect(el).not.toHaveStyle({ whiteSpace: "nowrap" });
  });

  it("expands via keyboard (Enter) on mobile", async () => {
    mockMatchMedia(true);
    render(<Probe />);
    const el = screen.getByRole("button");
    el.focus();
    await userEvent.keyboard("{Enter}");
    expect(screen.queryByRole("button")).toBeNull();
  });
});
