import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/renderWithProviders";
import { MAX_PINNED_ROUTES, togglePinnedRoute } from "../api/pinnedRoutes";
import { PinRouteButton } from "./PinRouteButton";

describe("PinRouteButton", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it("pins the route and shows it pressed, then unpins it", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PinRouteButton agencyId={9} code="21-1" />);
    const button = screen.getByRole("button", { name: "Pin" });
    expect(button).toHaveAttribute("aria-pressed", "false");
    await user.click(button);
    expect(button).toHaveAttribute("aria-pressed", "true");
    await user.click(button);
    expect(button).toHaveAttribute("aria-pressed", "false");
  });

  it("turns a new pin down once the list is full, and says why", () => {
    for (let i = 0; i < MAX_PINNED_ROUTES; i++) togglePinnedRoute(9, `R${i}`);
    renderWithProviders(<PinRouteButton agencyId={9} code="21-1" />);
    expect(screen.getByRole("button", { name: `Up to ${MAX_PINNED_ROUTES} pinned routes` })).toBeDisabled();
  });

  it("still unpins a pinned route when the list is full", () => {
    for (let i = 0; i < MAX_PINNED_ROUTES; i++) togglePinnedRoute(9, `R${i}`);
    renderWithProviders(<PinRouteButton agencyId={9} code="R0" />);
    expect(screen.getByRole("button", { name: "Pin" })).toBeEnabled();
  });
});
