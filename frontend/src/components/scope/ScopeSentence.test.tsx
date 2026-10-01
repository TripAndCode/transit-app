import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useSearchParams } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import * as hooks from "../../api/hooks";
import { ScopeSentence } from "./ScopeSentence";
import type { ScopeField } from "./scopePhrases";

function Probe() {
  const [params] = useSearchParams();
  return <div data-testid="params">{params.toString()}</div>;
}

function mount(search = "?from=2026-09-01&to=2026-09-28", applied?: Partial<Record<ScopeField, boolean>>) {
  vi.spyOn(hooks, "useAgencies").mockReturnValue({
    data: [{ agency_id: 1, agency_name: "Aomori", feed_url: "", static_url: null, latest_data_date: "2026-09-28" }],
    isLoading: false,
  } as never);
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isPending: false, isLoading: false } as never);
  return renderWithProviders(
    <MemoryRouter initialEntries={[`/agencies/1/time${search}`]}>
      <Routes>
        <Route
          path="/agencies/:agencyId/time"
          element={
            <>
              <ScopeSentence applied={applied} />
              <Probe />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

const params = () => new URLSearchParams(screen.getByTestId("params").textContent ?? "");
const region = () => screen.getByRole("region", { name: "What you're viewing" });

describe("ScopeSentence", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("says the scope as one sentence whose conditions are buttons", () => {
    mount();
    expect(region().textContent).toContain(
      "Viewing all routes of Aomori, 9/1 – 9/28, every day, all day, counting on-time as within 1 min",
    );
    for (const name of ["all routes", "9/1 – 9/28", "every day", "all day", "within 1 min"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: "Aomori" })).toBeNull();
  });

  it("opens a condition's popover, moves focus in, and returns it on Escape", async () => {
    mount();
    const token = screen.getByRole("button", { name: "every day" });
    await userEvent.click(token);
    const dialog = screen.getByRole("dialog", { name: "Days and timetable" });
    expect(dialog.contains(document.activeElement)).toBe(true);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(token).toHaveFocus();
  });

  it("closes on a click outside", async () => {
    mount();
    await userEvent.click(screen.getByRole("button", { name: "every day" }));
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("applies a change at once and keeps the popover open", async () => {
    mount();
    await userEvent.click(screen.getByRole("button", { name: "every day" }));
    await userEvent.click(screen.getByRole("button", { name: "Weekdays" }));
    expect(params().get("dow")).toBe("weekday");
    expect(screen.getByRole("dialog", { name: "Days and timetable" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Weekday" })).toBeInTheDocument();
  });

  it("greys the conditions the screen's data did not use", () => {
    mount(undefined, { dow: false, from: true });
    const days = screen.getByRole("button", { name: "every day" });
    expect(days).toHaveClass("scope-token--off");
    expect(days).toHaveAttribute("title", "This screen doesn't use this condition");
    expect(days).toHaveAccessibleDescription("This screen doesn't use this condition");
    expect(screen.getByRole("button", { name: "9/1 – 9/28" })).not.toHaveClass("scope-token--off");
  });

  it("greys nothing when the screen says nothing", () => {
    const { container } = mount();
    expect(container.querySelector(".scope-token--off")).toBeNull();
  });

  it("shows an hour deep link in the time condition, greyed where hours are not applied", () => {
    mount("?from=2026-09-01&to=2026-09-28&hour=7-9", { hour: false });
    expect(screen.getByRole("button", { name: "7:00–9:59" })).toHaveClass("scope-token--off");
  });

  it("pins every control inline and remembers it", async () => {
    const view = mount();
    const pin = screen.getByRole("button", { name: "Pin controls" });
    expect(pin).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("button", { name: "Weekdays" })).toBeNull();
    await userEvent.click(pin);
    expect(pin).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Weekdays" })).toBeInTheDocument();
    expect(localStorage.getItem("transit.scopePinned")).toBe("1");
    view.unmount();
    mount();
    expect(screen.getByRole("button", { name: "Pin controls" })).toHaveAttribute("aria-pressed", "true");
  });

  it("offers a reset once a condition differs from the default", async () => {
    mount();
    expect(screen.queryByRole("button", { name: "Reset conditions" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "every day" }));
    await userEvent.click(screen.getByRole("button", { name: "Weekdays" }));
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Reset conditions" }));
    expect(params().has("dow")).toBe(false);
    expect(params().has("from")).toBe(false);
    expect(params().has("to")).toBe(false);
  });

  it("adds a set timetable as its own condition", () => {
    mount("?from=2026-09-01&to=2026-09-28&service=%E5%B9%B3%E6%97%A5");
    expect(region().textContent).toContain("all day, Weekday timetable, counting");
  });
});
