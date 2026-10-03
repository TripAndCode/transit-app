import type { ComponentType } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useSearchParams } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import { AgencyDataEndProvider } from "../../api/AgencyDataEndProvider";
import * as hooks from "../../api/hooks";
import { useScope } from "../../api/scope";
import type { ScopeSummary } from "../../api/types";
import { DaysControl, PeriodControl, RoutesControl, TimeControl, ToleranceControl, type ControlProps } from "./scopeControls";

function Probe() {
  const [params] = useSearchParams();
  return <div data-testid="params">{params.toString()}</div>;
}

const SUMMARY: ScopeSummary = {
  earliest: "2026-06-01",
  latest: "2026-09-28",
  window_from: "2026-06-30",
  ctx: { from: "2026-09-01", to: "2026-09-28", dow: "all", time_band: "all" },
  days: [
    { date: "2026-09-27", avg_min: 1.2, samples: 100 },
    { date: "2026-09-28", avg_min: 2.0, samples: 100 },
  ],
  weekdays: [
    { dow: "mon", avg_min: 2.3, samples: 200 },
    { dow: "sat", avg_min: 5.1, samples: 50 },
  ],
  routes: [{ route_code: "101", avg_min: 1.0, samples: 300 }],
  tolerance: [
    { late_sec: 0, on_time_pct: 10 },
    { late_sec: 60, on_time_pct: 34 },
    { late_sec: 180, on_time_pct: 60 },
    { late_sec: 300, on_time_pct: 75 },
    { late_sec: 600, on_time_pct: 92 },
  ],
};

function Harness({ C, summary }: { C: ComponentType<ControlProps>; summary?: ScopeSummary }) {
  const [scope, update] = useScope();
  return <C scope={scope} update={update} summary={summary} />;
}

function mount(C: ComponentType<ControlProps>, search = "", summary?: ScopeSummary) {
  vi.spyOn(hooks, "useAgencies").mockReturnValue({
    data: [{ agency_id: 1, agency_name: "Aomori", feed_url: "", static_url: null, latest_data_date: "2026-09-28" }],
    isLoading: false,
  } as never);
  vi.spyOn(hooks, "useRoutes").mockReturnValue({
    data: [{ route_id: "r101", route_short_name: "1", route_long_name: "Coast", route_code: "101", trip_headsigns: [] }],
    isPending: false,
  } as never);
  renderWithProviders(
    <MemoryRouter initialEntries={[`/agencies/1/time${search}`]}>
      <Routes>
        <Route
          path="/agencies/:agencyId/time"
          element={
            <AgencyDataEndProvider>
              <Harness C={C} summary={summary} />
              <Probe />
            </AgencyDataEndProvider>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

const params = () => new URLSearchParams(screen.getByTestId("params").textContent ?? "");

describe("scope controls", () => {
  afterEach(() => vi.restoreAllMocks());

  describe("days", () => {
    it("turns one weekday off from every day", async () => {
      mount(DaysControl);
      await userEvent.click(screen.getByRole("button", { name: "Mon" }));
      expect(params().get("dow")).toBe("tue,wed,thu,fri,sat,sun");
    });

    it("refuses to turn the last day off", async () => {
      mount(DaysControl, "?dow=mon");
      await userEvent.click(screen.getByRole("button", { name: "Mon" }));
      expect(params().get("dow")).toBe("mon");
    });

    it("applies a preset and the timetable at once", async () => {
      mount(DaysControl);
      await userEvent.click(screen.getByRole("button", { name: "Weekdays" }));
      expect(params().get("dow")).toBe("weekday");
      await userEvent.selectOptions(screen.getByRole("combobox", { name: "Timetable" }), "平日");
      expect(params().get("service")).toBe("平日");
    });

    it("shows each weekday's mean delay once the data is in", () => {
      mount(DaysControl, "", SUMMARY);
      const mon = screen.getByRole("button", { name: "Mon 2.3 min" });
      expect(mon.querySelector(".scope-mini-bar")).toHaveStyle({ background: "var(--d1)" });
      expect(screen.getByRole("button", { name: "Sat 5.1 min" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Tue" })).toBeInTheDocument();
    });

    it("marks the selected days", () => {
      mount(DaysControl, "?dow=weekend");
      expect(screen.getByRole("button", { name: "Sat" })).toHaveAttribute("aria-pressed", "true");
      expect(screen.getByRole("button", { name: "Mon" })).toHaveAttribute("aria-pressed", "false");
    });
  });

  describe("time", () => {
    it("picks a band and drops an hour range", async () => {
      mount(TimeControl, "?hour=7-9");
      await userEvent.click(screen.getByRole("button", { name: "Morning (05–09)" }));
      expect(params().get("time_band")).toBe("morning");
      expect(params().has("hour")).toBe(false);
    });

    it("makes no promises about features to come", () => {
      mount(TimeControl);
      expect(screen.queryByText(/arrives with/)).not.toBeInTheDocument();
    });
  });

  describe("period", () => {
    afterEach(() => vi.useRealTimers());

    it("shows the default period as the 30-day preset when the data stops before yesterday", () => {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(new Date("2026-10-03T03:00:00Z"));
      mount(PeriodControl);
      expect(screen.getByRole("button", { name: "Last 30 days" })).toHaveAttribute("aria-pressed", "true");
    });

    it("anchors a preset on the agency's latest data", async () => {
      mount(PeriodControl);
      await userEvent.click(screen.getByRole("button", { name: "Last 7 days" }));
      expect(params().get("from")).toBe("2026-09-22");
      expect(params().get("to")).toBe("2026-09-28");
    });

    it("offers two weeks and the whole collection once the data is in", async () => {
      mount(PeriodControl, "", SUMMARY);
      await userEvent.click(screen.getByRole("button", { name: "Last 14 days" }));
      expect(params().get("from")).toBe("2026-09-15");
      await userEvent.click(screen.getByRole("button", { name: "Since collection began" }));
      expect(params().get("from")).toBe("2026-06-01");
      expect(params().get("to")).toBe("2026-09-28");
    });

    it("draws the daily bars once the data is in, and nothing without it", () => {
      mount(PeriodControl, "", SUMMARY);
      expect(screen.getByRole("img", { name: "Daily mean delay" })).toBeInTheDocument();
    });

    it("leaves out the bars and the collection preset without data", () => {
      mount(PeriodControl);
      expect(screen.queryByRole("img", { name: "Daily mean delay" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Since collection began" })).toBeNull();
    });

    it("ignores a start after the end", () => {
      mount(PeriodControl, "?from=2026-09-01&to=2026-09-10");
      fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-09-20" } });
      expect(params().get("from")).toBe("2026-09-01");
      fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-09-05" } });
      expect(params().get("from")).toBe("2026-09-05");
    });
  });

  describe("tolerance", () => {
    it("applies presets, with one minute as the default", async () => {
      mount(ToleranceControl, "?late=300");
      await userEvent.click(screen.getByRole("button", { name: "3 min" }));
      expect(params().get("late")).toBe("180");
      await userEvent.click(screen.getByRole("button", { name: "1 min" }));
      expect(params().has("late")).toBe(false);
    });

    it("names each preset's on-time share once the data is in", () => {
      mount(ToleranceControl, "", SUMMARY);
      expect(screen.getByRole("button", { name: "1 min 34%" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "3 min 60%" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "5 min 75%" })).toBeInTheDocument();
    });

    it("draws the on-time curve with a marker at the current tolerance", () => {
      mount(ToleranceControl, "?late=180", SUMMARY);
      const curve = screen.getByRole("img", { name: "On-time share by tolerance" });
      expect(curve.querySelector(".scope-curve__marker")?.getAttribute("cx")).toBe("72");
    });

    it("says the figures cover the whole day when a time band is set", () => {
      mount(ToleranceControl, "?time_band=morning", SUMMARY);
      expect(screen.getByText("These figures cover the whole day")).toBeInTheDocument();
    });

    it("stretches the curve to the slider's width and keeps the marker on it", () => {
      mount(ToleranceControl, "?late=900", SUMMARY);
      const curve = screen.getByRole("img", { name: "On-time share by tolerance" });
      expect(curve.getAttribute("preserveAspectRatio")).toBe("none");
      expect(curve.querySelector(".scope-curve__marker")?.getAttribute("cx")).toBe("240");
    });

    it("reads out the share at the current tolerance", () => {
      mount(ToleranceControl, "?late=180", SUMMARY);
      expect(screen.getByText("3 min: 60%")).toBeInTheDocument();
    });

    it("leaves out the whole-day note when there are no figures", () => {
      const empty: ScopeSummary = { ...SUMMARY, days: [], weekdays: [], routes: [], tolerance: [] };
      mount(ToleranceControl, "?time_band=morning", empty);
      expect(screen.queryByText("These figures cover the whole day")).toBeNull();
    });

    it("says the figures cover every stop when a stop is set", () => {
      mount(ToleranceControl, "?stop=S1", SUMMARY);
      expect(screen.getByText("These figures cover the whole day, every stop and both directions")).toBeInTheDocument();
    });

    it("draws no curve without the data", () => {
      mount(ToleranceControl);
      expect(screen.queryByRole("img", { name: "On-time share by tolerance" })).toBeNull();
    });

    it("writes the slider only when the drag ends", () => {
      mount(ToleranceControl);
      const slider = screen.getByRole("slider", { name: "Counted on time up to" });
      fireEvent.change(slider, { target: { value: "240" } });
      expect(params().has("late")).toBe(false);
      fireEvent.pointerUp(slider);
      expect(params().get("late")).toBe("240");
    });
  });

  describe("routes with data", () => {
    it("shows a route's mean delay beside it", () => {
      mount(RoutesControl, "", SUMMARY);
      expect(screen.getByText("1.0 min")).toBeInTheDocument();
    });

    it("shows nothing beside a route without the data", () => {
      mount(RoutesControl);
      expect(screen.queryByText("1.0 min")).toBeNull();
    });
  });

  describe("routes", () => {
    it("names its search box for assistive technology, not by placeholder alone", () => {
      mount(RoutesControl);
      expect(screen.getByRole("textbox", { name: "Search routes" })).toBeInTheDocument();
    });

    it("adds a route and clears the filter with the last one", async () => {
      mount(RoutesControl);
      await userEvent.click(screen.getByRole("button", { name: /^1 ?Coast$/ }));
      expect(params().get("routes")).toBe("101");
      await userEvent.click(screen.getByRole("button", { name: /^1 ?Coast$/ }));
      expect(params().has("routes")).toBe(false);
    });
  });
});
