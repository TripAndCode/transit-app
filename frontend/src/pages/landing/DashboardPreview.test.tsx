import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { DashboardPreview } from "./DashboardPreview";

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
