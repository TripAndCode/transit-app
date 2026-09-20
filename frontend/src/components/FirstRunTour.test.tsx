import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
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
});
