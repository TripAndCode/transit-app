import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useSearchParams } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import * as hooks from "../../api/hooks";
import i18n from "../../i18n";
import { useTopmostEscape } from "../../hooks/useFocusTrap";
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
      "Viewing all routes of Aomori, 9/1 – 9/28, every day, all hours, counting on-time as within 1 min",
    );
    for (const name of ["all routes", "9/1 – 9/28", "every day", "all hours", "within 1 min"]) {
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

  it("keeps the popover out of any paragraph, since it holds block content", async () => {
    mount();
    await userEvent.click(screen.getByRole("button", { name: "all hours" }));
    expect(screen.getByRole("dialog", { name: "Time of day" }).closest("p")).toBeNull();
  });

  it("closes when Tab walks focus out of the popover", async () => {
    mount();
    await userEvent.click(screen.getByRole("button", { name: "within 1 min" }));
    screen.getByRole("slider").focus();
    await userEvent.tab();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "within 1 min" })).not.toHaveFocus();
  });

  it("leaves an Escape to an overlay opened over it", async () => {
    const onTop = vi.fn();
    function Overlay() {
      useTopmostEscape(true, onTop);
      return null;
    }
    function WithOverlay() {
      const [shown, setShown] = useState(false);
      return (
        <>
          <ScopeSentence />
          <button type="button" onClick={() => setShown(true)}>
            open-overlay
          </button>
          {shown && <Overlay />}
        </>
      );
    }
    vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: [], isLoading: false } as never);
    vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isPending: false, isLoading: false } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/1/time"]}>
        <Routes>
          <Route path="/agencies/:agencyId/time" element={<WithOverlay />} />
        </Routes>
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole("button", { name: "all hours" }));
    fireEvent.click(screen.getByRole("button", { name: "open-overlay" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onTop).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("dialog", { name: "Time of day" })).toBeInTheDocument();
  });

  it("keeps focus in the section when a condition clears itself", async () => {
    mount("?from=2026-09-01&to=2026-09-28&stop=S1");
    await userEvent.click(screen.getByRole("button", { name: "stop S1" }));
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(params().has("stop")).toBe(false);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(region()).toHaveFocus();
  });

  it("shows a set early tolerance and lets it be cleared", async () => {
    mount("?from=2026-09-01&to=2026-09-28&early=120");
    await userEvent.click(screen.getByRole("button", { name: "early by up to 2 min" }));
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(params().has("early")).toBe(false);
    expect(region()).toHaveFocus();
  });

  it("lists the optional conditions in the order the words give them", () => {
    mount("?from=2026-09-01&to=2026-09-28&service=%E5%B9%B3%E6%97%A5&stop=S1&dir=0&early=60");
    expect(region().textContent).toContain(
      "all hours, Weekday timetable, stop S1, direction 0, early by up to 1 min, counting on-time",
    );
  });

  it("keeps focus in the section after a reset", async () => {
    mount("?from=2026-09-01&to=2026-09-28&dow=weekday");
    await userEvent.click(screen.getByRole("button", { name: "Reset conditions" }));
    expect(region()).toHaveFocus();
  });

  it("keeps a token's trailing punctuation on the same line", () => {
    mount();
    const token = screen.getByRole("button", { name: "every day" });
    expect(token.closest(".scope-nowrap")?.textContent).toBe("every day,");
  });

  it("reads as Japanese grammar in Japanese", async () => {
    await i18n.changeLanguage("ja");
    try {
      mount();
      expect(screen.getByRole("region", { name: "表示の条件" }).textContent).toContain(
        "Aomoriの全路線を、9/1〜9/28のすべての曜日・全時間帯で、定時は1分以内として見る",
      );
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("fetches the controls' data only once a popover opens", async () => {
    const summary = vi.spyOn(hooks, "useScopeSummary");
    mount();
    expect(summary.mock.calls.every((call) => call[2] === false)).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "every day" }));
    expect(summary.mock.calls.at(-1)?.[2]).toBe(true);
  });

  it("does not fetch for a control that draws no data", async () => {
    const summary = vi.spyOn(hooks, "useScopeSummary");
    mount();
    await userEvent.click(screen.getByRole("button", { name: "all hours" }));
    expect(summary.mock.calls.at(-1)?.[2]).toBe(false);
  });

  it("fetches while the strip is pinned", async () => {
    const summary = vi.spyOn(hooks, "useScopeSummary");
    mount();
    await userEvent.click(screen.getByRole("button", { name: "Show all filters" }));
    expect(summary.mock.calls.at(-1)?.[2]).toBe(true);
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
    expect(screen.getByRole("button", { name: "weekdays" })).toBeInTheDocument();
  });

  it("greys the set conditions the screen's data did not use", () => {
    mount("?from=2026-09-01&to=2026-09-28&dow=weekday", { dow: false, from: true });
    const days = screen.getByRole("button", { name: "weekdays" });
    expect(days).toHaveClass("scope-token--off");
    expect(days).toHaveAttribute("title", "This screen doesn't use this condition");
    expect(days).toHaveAccessibleDescription("This screen doesn't use this condition");
    expect(screen.getByRole("button", { name: "9/1 – 9/28" })).not.toHaveClass("scope-token--off");
  });

  it("says in words which set conditions this screen doesn't use", () => {
    mount("?from=2026-09-01&to=2026-09-28&dow=weekday", { dow: false, from: true });
    expect(screen.getByText("Not used on this screen: Days and timetable")).toBeVisible();
  });

  it("says nothing extra when every set condition is used", () => {
    mount("?from=2026-09-01&to=2026-09-28&dow=weekday", { dow: true, from: true });
    expect(screen.queryByText(/Not used on this screen/)).toBeNull();
  });

  it("leaves out the on-time clause where the screen doesn't use it and nothing sets it", () => {
    mount(undefined, { late: false });
    expect(region().textContent).not.toContain("counting on-time");
    expect(screen.queryByRole("button", { name: "within 1 min" })).toBeNull();
  });

  it("keeps a set on-time tolerance in the sentence, greyed where unused", () => {
    mount("?from=2026-09-01&to=2026-09-28&late=180", { late: false });
    expect(screen.getByRole("button", { name: "within 3 min" })).toHaveClass("scope-token--off");
  });

  it("leaves a condition at its default ungreyed, since it filters nothing", () => {
    const { container } = mount(undefined, { dow: false, late: false, routes: false, time_band: false });
    expect(container.querySelector(".scope-token--off")).toBeNull();
  });

  it("greys a pinned control the same way", async () => {
    mount("?from=2026-09-01&to=2026-09-28&dow=weekday", { dow: false });
    await userEvent.click(screen.getByRole("button", { name: "Show all filters" }));
    expect(screen.getByText("Days and timetable")).toHaveClass("scope-strip__label--off");
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
    const pin = screen.getByRole("button", { name: "Show all filters" });
    expect(pin).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("button", { name: "Weekdays" })).toBeNull();
    await userEvent.click(pin);
    expect(pin).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Weekdays" })).toBeInTheDocument();
    expect(localStorage.getItem("transit.scopePinned")).toBe("1");
    view.unmount();
    mount();
    expect(screen.getByRole("button", { name: "Show all filters" })).toHaveAttribute("aria-pressed", "true");
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
    expect(region().textContent).toContain("all hours, Weekday timetable, counting");
  });
});
