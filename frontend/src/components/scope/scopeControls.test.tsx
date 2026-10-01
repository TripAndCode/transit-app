import type { ComponentType } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useSearchParams } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import * as hooks from "../../api/hooks";
import { useScope } from "../../api/scope";
import { DaysControl, PeriodControl, RoutesControl, TimeControl, ToleranceControl, type ControlProps } from "./scopeControls";

function Probe() {
  const [params] = useSearchParams();
  return <div data-testid="params">{params.toString()}</div>;
}

function Harness({ C }: { C: ComponentType<ControlProps> }) {
  const [scope, update] = useScope();
  return <C scope={scope} update={update} />;
}

function mount(C: ComponentType<ControlProps>, search = "") {
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
            <>
              <Harness C={C} />
              <Probe />
            </>
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

    it("says hour filtering is not available yet", () => {
      mount(TimeControl);
      expect(screen.getByText("Hour-by-hour filtering arrives with the hourly aggregate")).toBeInTheDocument();
    });
  });

  describe("period", () => {
    it("anchors a preset on the agency's latest data", async () => {
      mount(PeriodControl);
      await userEvent.click(screen.getByRole("button", { name: "Last 7 days" }));
      expect(params().get("from")).toBe("2026-09-22");
      expect(params().get("to")).toBe("2026-09-28");
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

    it("writes the slider only when the drag ends", () => {
      mount(ToleranceControl);
      const slider = screen.getByRole("slider", { name: "Counted on time up to" });
      fireEvent.change(slider, { target: { value: "240" } });
      expect(params().has("late")).toBe(false);
      fireEvent.pointerUp(slider);
      expect(params().get("late")).toBe("240");
    });
  });

  describe("routes", () => {
    it("adds a route and clears the filter with the last one", async () => {
      mount(RoutesControl);
      await userEvent.click(screen.getByRole("button", { name: /^1 ?Coast$/ }));
      expect(params().get("routes")).toBe("101");
      await userEvent.click(screen.getByRole("button", { name: /^1 ?Coast$/ }));
      expect(params().has("routes")).toBe(false);
    });
  });
});
