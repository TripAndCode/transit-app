import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useSearchParams } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import * as hooks from "../../api/hooks";
import i18n from "../../i18n";
import { DataEndContext, ScopeRouteContext } from "../../api/scope";
import { useTopmostEscape } from "../../hooks/useFocusTrap";
import { ScopeHeaderProvider, ScopePageHeader } from "./ScopePageHeader";
import { ScopeBar } from "./ScopeBar";
import type { ScopeField } from "./scopePhrases";

function Probe() {
  const [params] = useSearchParams();
  return <div data-testid="params">{params.toString()}</div>;
}

/** The default period is 9/1-9/30: thirty days ending on the data's last day. */
function mount(
  search = "?from=2026-09-01&to=2026-09-30",
  applied?: Partial<Record<ScopeField, boolean>>,
  page = <ScopeBar applied={applied} />,
  routes: unknown[] = [],
) {
  vi.spyOn(hooks, "useAgencies").mockReturnValue({
    data: [{ agency_id: 1, agency_name: "Aomori", feed_url: "", static_url: null, latest_data_date: "2026-09-30" }],
    isLoading: false,
  } as never);
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: routes, isPending: false, isLoading: false } as never);
  return renderWithProviders(
    <DataEndContext value="2026-09-30">
      <MemoryRouter initialEntries={[`/agencies/1/time${search}`]}>
        <Routes>
          <Route
            path="/agencies/:agencyId/time"
            element={
              <>
                {page}
                <Probe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </DataEndContext>,
  );
}

const params = () => new URLSearchParams(screen.getByTestId("params").textContent ?? "");
const region = () => screen.getByRole("region", { name: "What you're viewing" });
/** The bar's condition labels, in the order it shows them. */
const labels = () => [...document.querySelectorAll(".scope-bar__label")].map((el) => el.textContent);

const TWO_ROUTES = [
  { route_id: "r101", route_short_name: "1", route_long_name: "Coast", route_code: "101", trip_headsigns: [] },
  { route_id: "r102", route_short_name: "2", route_long_name: "Hill", route_code: "102", trip_headsigns: [] },
];

/** `mount`, but with two real routes so the routes condition's own control has
 *  something to pick from. */
function mountRoutes(search = "?from=2026-09-01&to=2026-09-30") {
  return mount(search, undefined, undefined, TWO_ROUTES);
}

describe("ScopeBar", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("shows each condition as a labelled control, when before what", () => {
    mount();
    expect(labels()).toEqual(["Period", "Days", "Time", "Routes", "On time"]);
    for (const name of ["Period 9/1 – 9/30 30 days", "Days All", "Time All day", "Routes All", "On time within 5 min"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(region().textContent).not.toContain("Aomori");
  });

  it("disables its controls, Reset and an already open popover's controls", () => {
    function Disabling() {
      const [disabled, setDisabled] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setDisabled(true)}>
            disable
          </button>
          <ScopeBar disabled={disabled} />
        </>
      );
    }
    mount("?from=2026-09-01&to=2026-09-30&dow=weekday", undefined, <Disabling />);
    fireEvent.click(screen.getByRole("button", { name: "Time All day" }));
    const popover = screen.getByRole("dialog", { name: "Time of day" });
    fireEvent.click(screen.getByRole("button", { name: "disable" }));
    expect(screen.getByRole("dialog", { name: "Time of day" })).toBe(popover);
    for (const radio of within(popover).getAllByRole("radio")) expect(radio).toBeDisabled();
    expect(screen.getByRole("button", { name: "Days weekdays" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reset conditions" })).toBeDisabled();
  });

  it("opens a condition's popover, moves focus in, and returns it on Escape", async () => {
    mount();
    const control = screen.getByRole("button", { name: "Days All" });
    await userEvent.click(control);
    expect(control).toHaveAttribute("aria-expanded", "true");
    const dialog = screen.getByRole("dialog", { name: "Days and timetable" });
    expect(dialog.contains(document.activeElement)).toBe(true);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(control).toHaveFocus();
  });

  it("opens a popover on its checked option rather than the first one", async () => {
    mount();
    await userEvent.click(screen.getByRole("button", { name: "Period 9/1 – 9/30 30 days" }));
    expect(screen.getByRole("radio", { name: "Last 30 days" })).toHaveFocus();
  });

  it("closes when Tab walks focus out of the popover", async () => {
    mount();
    await userEvent.click(screen.getByRole("button", { name: "On time within 5 min" }));
    screen.getByRole("radio", { name: "Not on time if more than 1 min early" }).focus();
    await userEvent.tab();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "On time within 5 min" })).not.toHaveFocus();
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
          <ScopeBar />
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
    await userEvent.click(screen.getByRole("button", { name: "Time All day" }));
    fireEvent.click(screen.getByRole("button", { name: "open-overlay" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onTop).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("dialog", { name: "Time of day" })).toBeInTheDocument();
  });

  it("keeps focus in the section when a condition clears itself", async () => {
    mount("?from=2026-09-01&to=2026-09-30&stop=S1");
    await userEvent.click(screen.getByRole("button", { name: "Stop selected stop" }));
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(params().has("stop")).toBe(false);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(region()).toHaveFocus();
  });

  it("shows a set early tolerance and lets it be cleared", async () => {
    mount("?from=2026-09-01&to=2026-09-30&early=120");
    await userEvent.click(screen.getByRole("button", { name: "Early up to 2\u00a0min" }));
    await userEvent.click(screen.getByRole("radio", { name: "Not on time if more than 1 min early" }));
    expect(params().has("early")).toBe(false);
    expect(region()).toHaveFocus();
  });

  it("says when every early departure counts, the legacy definition's early side", () => {
    mount("?from=2026-09-01&to=2026-09-30&early=any&late=60");
    expect(screen.getByRole("button", { name: "Early All on time" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "On time within 1 min" })).toBeInTheDocument();
  });

  it("adds each optional condition only while it is set, in the bar's order", () => {
    mount("?from=2026-09-01&to=2026-09-30&service=%E5%B9%B3%E6%97%A5&stop=S1&dir=0&early=60");
    expect(labels()).toEqual(["Period", "Days", "Timetable", "Time", "Routes", "Stop", "Direction", "On time", "Early"]);
    expect(screen.getByRole("button", { name: "Timetable Weekday" })).toBeInTheDocument();
  });

  it("keeps focus in the section after a reset", async () => {
    mount("?from=2026-09-01&to=2026-09-30&dow=weekday");
    await userEvent.click(screen.getByRole("button", { name: "Reset conditions" }));
    expect(region()).toHaveFocus();
  });

  it("names its conditions in Japanese", async () => {
    await i18n.changeLanguage("ja");
    try {
      mount();
      expect(screen.getByRole("region", { name: "表示の条件" })).toBeInTheDocument();
      expect(labels()).toEqual(["期間", "曜日", "時間帯", "路線", "定時の幅"]);
      for (const name of ["曜日 すべて", "時間帯 全日", "路線 すべて"]) {
        expect(screen.getByRole("button", { name })).toBeInTheDocument();
      }
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("fetches the controls' data only once a popover opens", async () => {
    const summary = vi.spyOn(hooks, "useScopeSummary");
    mount();
    expect(summary.mock.calls.every((call) => call[2] === false)).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Days All" }));
    expect(summary.mock.calls.at(-1)?.[2]).toBe(true);
  });

  it("does not fetch for a control that draws no data", async () => {
    const summary = vi.spyOn(hooks, "useScopeSummary");
    mount();
    await userEvent.click(screen.getByRole("button", { name: "Time All day" }));
    expect(summary.mock.calls.at(-1)?.[2]).toBe(false);
  });

  it("never fetches on a screen that counts by no agency's days", async () => {
    const summary = vi.spyOn(hooks, "useScopeSummary");
    mount(undefined, undefined, <ScopeBar figures={false} />);
    await userEvent.click(screen.getByRole("button", { name: "Days All" }));
    expect(summary.mock.calls.every((call) => call[2] === false)).toBe(true);
  });

  it("fetches while the strip is pinned", async () => {
    const summary = vi.spyOn(hooks, "useScopeSummary");
    mount();
    await userEvent.click(screen.getByRole("button", { name: "Show all filters" }));
    expect(summary.mock.calls.at(-1)?.[2]).toBe(true);
  });

  it("closes on a click outside", async () => {
    mount();
    await userEvent.click(screen.getByRole("button", { name: "Days All" }));
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("applies a change at once and keeps the popover open", async () => {
    mount();
    await userEvent.click(screen.getByRole("button", { name: "Days All" }));
    await userEvent.click(screen.getByRole("radio", { name: "Weekdays" }));
    expect(params().get("dow")).toBe("weekday");
    expect(screen.getByRole("dialog", { name: "Days and timetable" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Days weekdays" })).toBeInTheDocument();
  });

  it("leaves out the conditions the screen's data did not use", () => {
    mount("?from=2026-09-01&to=2026-09-30&dow=weekday&late=180", { from: true, to: true, dow: false, service: false, late: false });
    expect(labels()).toEqual(["Period", "Time", "Routes"]);
    expect(screen.queryByRole("button", { name: /^Days/ })).toBeNull();
    expect(screen.queryByText(/Not used on this screen/)).toBeNull();
  });

  it("keeps a condition whose control sets a field the screen did use", () => {
    mount(undefined, { dow: false, service: true, time_band: false, hour: true });
    expect(labels()).toContain("Days");
    expect(labels()).toContain("Time");
  });

  it("shows every condition while the screen has not said what it used", () => {
    mount("?from=2026-09-01&to=2026-09-30&stop=S1");
    expect(labels()).toEqual(["Period", "Days", "Time", "Routes", "Stop", "On time"]);
  });

  it("offers no reset for a change the screen does not use", () => {
    mount("?from=2026-09-01&to=2026-09-30&dow=weekday", { dow: false, service: false });
    expect(screen.queryByRole("button", { name: "Reset conditions" })).toBeNull();
  });

  it("resets only the conditions it shows, leaving one the screen hides for the screens that use it", async () => {
    mount("?from=2026-09-01&to=2026-09-30&dow=weekday&time_band=morning", { dow: false, service: false });
    await userEvent.click(screen.getByRole("button", { name: "Reset conditions" }));
    expect(params().has("time_band")).toBe(false);
    expect(params().get("dow")).toBe("weekday");
  });

  it("keeps a route's own page on its route through a reset", async () => {
    mount(
      "?from=2026-09-01&to=2026-09-30&routes=101&dow=weekday",
      undefined,
      <ScopeRouteContext value="101">
        <ScopeBar />
      </ScopeRouteContext>,
      TWO_ROUTES,
    );
    await userEvent.click(screen.getByRole("button", { name: "Reset conditions" }));
    expect(params().has("dow")).toBe(false);
    expect(params().get("routes")).toBe("101");
  });

  it("reads the band where the screen bands the day but takes no hour, in the bar and its control", async () => {
    mount("?from=2026-09-01&to=2026-09-30&hour=7-9", { hour: false, time_band: true });
    const time = screen.getByRole("button", { name: "Time All day" });
    expect(time).not.toHaveClass("scope-bar__control--changed");
    await userEvent.click(time);
    expect(screen.getByRole("radio", { name: "All hours" })).toBeChecked();
  });

  it("keeps the screen's last answer while it reloads, rather than showing every condition", () => {
    function Reloading() {
      const [loading, setLoading] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setLoading(true)}>
            reload
          </button>
          <ScopeBar applied={loading ? undefined : { from: true, to: true, dow: false, service: false }} />
        </>
      );
    }
    mount(undefined, undefined, <Reloading />);
    expect(labels()).not.toContain("Days");
    fireEvent.click(screen.getByRole("button", { name: "reload" }));
    expect(labels()).not.toContain("Days");
    expect(labels()).toContain("Period");
  });

  it("names a direction by its letter under its label", () => {
    mount("?from=2026-09-01&to=2026-09-30&dir=1");
    expect(screen.getByRole("button", { name: "Direction B" })).toBeInTheDocument();
  });

  it("shows an hour deep link where the screen takes hours", () => {
    mount("?from=2026-09-01&to=2026-09-30&hour=7-9");
    expect(screen.getByRole("button", { name: "Time 7:00–9:59" })).toHaveClass("scope-bar__control--changed");
  });

  it("pins only the conditions the bar shows", async () => {
    mount(undefined, { from: true, to: true, dow: false, service: false });
    await userEvent.click(screen.getByRole("button", { name: "Show all filters" }));
    const strip = document.querySelector(".scope-strip")!;
    expect(within(strip as HTMLElement).queryByText("Days and timetable")).toBeNull();
    expect(within(strip as HTMLElement).getByText("Time of day")).toBeInTheDocument();
  });

  it("pins every control inline and remembers it", async () => {
    const view = mount();
    const pin = screen.getByRole("button", { name: "Show all filters" });
    expect(pin).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("radio", { name: "Weekdays" })).toBeNull();
    await userEvent.click(pin);
    expect(pin).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("radio", { name: "Weekdays" })).toBeInTheDocument();
    expect(localStorage.getItem("transit.scopePinned")).toBe("1");
    view.unmount();
    mount();
    expect(screen.getByRole("button", { name: "Show all filters" })).toHaveAttribute("aria-pressed", "true");
  });

  it("ends the bar with a reset once a condition differs from the default", async () => {
    const { container } = mount();
    expect(screen.queryByRole("button", { name: "Reset conditions" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Days All" }));
    await userEvent.click(screen.getByRole("radio", { name: "Weekdays" }));
    await userEvent.keyboard("{Escape}");
    const reset = screen.getByRole("button", { name: "Reset conditions" });
    expect(reset).toHaveTextContent(/^Reset$/);
    expect(container.querySelector(".scope-bar")?.lastElementChild).toBe(reset);
    await userEvent.click(reset);
    expect(params().has("dow")).toBe(false);
    expect(params().has("from")).toBe(false);
    expect(params().has("to")).toBe(false);
  });

  it("marks the conditions changed from the default, and only those", () => {
    mount("?from=2026-09-01&to=2026-09-30&dow=weekday&time_band=morning");
    for (const name of ["Days weekdays", "Time Morning (05–09)"]) {
      expect(screen.getByRole("button", { name })).toHaveClass("scope-bar__control", "scope-bar__control--changed");
    }
    for (const name of ["Routes All", "Period 9/1 – 9/30 30 days", "On time within 5 min"]) {
      expect(screen.getByRole("button", { name })).not.toHaveClass("scope-bar__control--changed");
    }
  });

  it("counts a period other than the default as a change", () => {
    mount("?from=2026-09-10&to=2026-09-30");
    expect(screen.getByRole("button", { name: "Period 9/10 – 9/30 21 days" })).toHaveClass("scope-bar__control--changed");
    expect(screen.getByRole("button", { name: "Reset conditions" })).toBeInTheDocument();
  });

  it("shows the period's length as a quiet hint inside its control", () => {
    mount();
    const hint = screen.getByRole("button", { name: "Period 9/1 – 9/30 30 days" }).querySelector(".scope-bar__hint");
    expect(hint).toHaveTextContent("30 days");
  });

  it("keeps its saved views and pin with the bar when the page gives them no other place", () => {
    mount();
    expect(region()).toContainElement(screen.getByRole("button", { name: "Show all filters" }));
  });

  it("puts its saved views and pin on the page heading's row when the page has one", async () => {
    const { container } = mount(
      undefined,
      undefined,
      <ScopeHeaderProvider>
        <ScopePageHeader title="Time" />
        <ScopeBar />
      </ScopeHeaderProvider>,
    );
    const header = container.querySelector("header")!;
    const pin = screen.getByRole("button", { name: "Show all filters" });
    expect(header).toContainElement(pin);
    expect(region()).not.toContainElement(pin);
    await userEvent.click(pin);
    expect(region()).toContainElement(screen.getByRole("radio", { name: "Weekdays" }));
  });
});

describe("ScopeBar routes condition", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("stages a pick behind Apply, then shows it as the condition's value and closes back to it", async () => {
    mountRoutes();
    const control = screen.getByRole("button", { name: "Routes All" });
    await userEvent.click(control);
    const dialog = screen.getByRole("dialog", { name: "Routes" });
    await userEvent.click(within(dialog).getByRole("button", { name: /^1 ?Coast$/ }));
    expect(params().has("routes")).toBe(false);
    await userEvent.click(within(dialog).getByRole("button", { name: "Apply · 1 route" }));
    expect(params().get("routes")).toBe("101");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: /^Routes 1 ?Coast$/ })).toHaveFocus();
  });

  it("discards the draft on Escape, leaving the committed pick alone", async () => {
    mountRoutes("?from=2026-09-01&to=2026-09-30&routes=101");
    await userEvent.click(screen.getByRole("button", { name: /^Routes 1 ?Coast$/ }));
    const dialog = screen.getByRole("dialog", { name: "Routes" });
    await userEvent.click(within(dialog).getByRole("button", { name: /^2 ?Hill$/ }));
    await userEvent.keyboard("{Escape}");
    expect(params().get("routes")).toBe("101");
    expect(screen.queryByRole("dialog")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /^Routes 1 ?Coast$/ }));
    const reopened = screen.getByRole("dialog", { name: "Routes" });
    expect(within(reopened).getByRole("checkbox", { name: /^2 ?Hill$/ })).not.toBeChecked();
  });

  it("discards the draft on a click outside the popover", async () => {
    mountRoutes("?from=2026-09-01&to=2026-09-30&routes=101");
    await userEvent.click(screen.getByRole("button", { name: /^Routes 1 ?Coast$/ }));
    const dialog = screen.getByRole("dialog", { name: "Routes" });
    await userEvent.click(within(dialog).getByRole("button", { name: /^2 ?Hill$/ }));
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(params().get("routes")).toBe("101");
  });

  it("keeps the popover open when removing a tray chip or clearing it, rather than reading the click as outside", async () => {
    mountRoutes();
    await userEvent.click(screen.getByRole("button", { name: "Routes All" }));
    const dialog = screen.getByRole("dialog", { name: "Routes" });
    await userEvent.click(within(dialog).getByRole("button", { name: /^1 ?Coast$/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: /^2 ?Hill$/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove route 1 Coast (101)" }));
    expect(screen.getByRole("dialog", { name: "Routes" })).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Clear" }));
    expect(screen.getByRole("dialog", { name: "Routes" })).toBeInTheDocument();
    expect(params().has("routes")).toBe(false);
  });

  it("keeps the pinned strip's own draft in step with a commit made through the bar's own popover", async () => {
    mountRoutes();
    await userEvent.click(screen.getByRole("button", { name: "Show all filters" }));
    const strip = () => within(document.querySelector(".scope-strip")!);
    // The days control shares the strip and stages its own Apply, so the
    // routes cell is scoped by its search box, not the strip as a whole.
    const routesCell = () => within(strip().getByRole("textbox", { name: "Search routes" }).closest(".scope-strip__cell")!);
    await userEvent.click(strip().getByRole("button", { name: /^2 ?Hill$/ }));
    expect(routesCell().getByRole("button", { name: /^Apply/ })).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: "Routes All" }));
    const dialog = screen.getByRole("dialog", { name: "Routes" });
    await userEvent.click(within(dialog).getByRole("button", { name: /^1 ?Coast$/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Apply · 1 route" }));
    expect(params().get("routes")).toBe("101");

    expect(strip().getByRole("checkbox", { name: /^1 ?Coast$/ })).toBeChecked();
    expect(strip().getByRole("checkbox", { name: /^2 ?Hill$/ })).not.toBeChecked();
    expect(routesCell().getByRole("button", { name: /^Apply/ })).toBeDisabled();
  });

  it("names several picked routes by their count", () => {
    mountRoutes("?from=2026-09-01&to=2026-09-30&routes=101,102");
    expect(screen.getByRole("button", { name: "Routes 2 routes" })).toBeInTheDocument();
  });

  it("asks for no route names on a screen that neither filters by route nor saves views", () => {
    mount(undefined, { routes: false }, <ScopeBar applied={{ routes: false }} actions={false} />);
    expect(vi.mocked(hooks.useRoutes).mock.calls.every((call) => call[0] == null)).toBe(true);
  });
});
