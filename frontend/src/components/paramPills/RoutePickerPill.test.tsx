import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/renderWithProviders";
import { RoutePickerPill } from "./RoutePickerPill";
import * as hooks from "../../api/hooks";

function mockRoutes() {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({
    data: [{ route_id: 1, route_code: "J20", route_short_name: "中筒井線", route_long_name: null }],
    isLoading: false,
  } as never);
}

function setup(overrides: Partial<Parameters<typeof RoutePickerPill>[0]> = {}) {
  const onChange = vi.fn();
  renderWithProviders(
    <RoutePickerPill
      label="Route"
      value={null}
      agencyId={1}
      placeholder="Select a route"
      onChange={onChange}
      {...overrides}
    />,
  );
  return { onChange };
}

describe("RoutePickerPill popover direction", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("opens downward (no rp-pop-up class) when there's plenty of room below", async () => {
    mockRoutes();
    Object.defineProperty(window, "innerHeight", { value: 1000, configurable: true });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      top: 50,
      bottom: 80,
      left: 0,
      right: 0,
      width: 0,
      height: 30,
      x: 0,
      y: 50,
      toJSON() {},
    });
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole("button", { name: "Route" }));
    expect(screen.getByRole("listbox")).not.toHaveClass("rp-pop-up");
  });

  it("flips upward (rp-pop-up class) when the trigger sits near the bottom of the viewport", async () => {
    mockRoutes();
    Object.defineProperty(window, "innerHeight", { value: 800, configurable: true });
    // Trigger near the bottom (e.g. docked in QuestionDock) — well under
    // 300px of space below, but plenty of room above.
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      top: 750,
      bottom: 780,
      left: 0,
      right: 0,
      width: 0,
      height: 30,
      x: 0,
      y: 750,
      toJSON() {},
    });
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole("button", { name: "Route" }));
    expect(screen.getByRole("listbox")).toHaveClass("rp-pop-up");
  });
});
