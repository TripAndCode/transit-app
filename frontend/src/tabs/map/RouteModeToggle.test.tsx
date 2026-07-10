import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/renderWithProviders";
import { RouteModeToggle } from "./RouteModeToggle";

describe("RouteModeToggle", () => {
  it("renders both mode buttons", () => {
    renderWithProviders(<RouteModeToggle mode="trend" onModeChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Historical trend" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "By hour" })).toBeInTheDocument();
  });

  it("marks the current mode's button as pressed", () => {
    renderWithProviders(<RouteModeToggle mode="hourly" onModeChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "By hour" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Historical trend" })).toHaveAttribute("aria-pressed", "false");
  });

  it("calls onModeChange with the clicked mode", async () => {
    const user = userEvent.setup();
    const onModeChange = vi.fn();
    renderWithProviders(<RouteModeToggle mode="trend" onModeChange={onModeChange} />);
    await user.click(screen.getByRole("button", { name: "By hour" }));
    expect(onModeChange).toHaveBeenCalledWith("hourly");
  });
});
