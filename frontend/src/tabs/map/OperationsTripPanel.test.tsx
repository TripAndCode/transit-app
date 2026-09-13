import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { TFunction } from "i18next";
import type { LiveTrip, LiveTripProgressResponse } from "../../api/types";
import { OperationsTripPanel } from "./OperationsTripPanel";

const t = ((key: string, values?: Record<string, unknown>) => values ? `${key}:${JSON.stringify(values)}` : key) as TFunction;

const trip = (id: string, time: string, delay: number): LiveTrip => ({
  trip_id: id, route_code: "B1", service_type: null, scheduled_time: time,
  dep_delay: delay, captured_at: "2026-09-12T06:02:00Z", stop_id: "S2",
  stop_sequence: 2, stop_name: "市役所前", stop_lat: 40.82, stop_lon: 140.72,
  headsign: "新町", direction_id: 1,
});

const trips = [trip("T1", "15:10:00", 180), trip("T2", "15:20:00", 60)];
const progress: LiveTripProgressResponse = {
  trip_id: "T1", route_code: "B1", headsign: "新町", direction_id: 1,
  latest_captured_at: "2026-09-12T06:02:00Z",
  stops: [
    { stop_sequence: 1, stop_id: "S1", stop_name: "中央病院", stop_lat: 40.81, stop_lon: 140.71, scheduled_time: "15:05:00", dep_delay: 60, reported_at: "2026-09-12T06:00:00Z" },
    { stop_sequence: 2, stop_id: "S2", stop_name: "市役所前", stop_lat: 40.82, stop_lon: 140.72, scheduled_time: "15:10:00", dep_delay: 180, reported_at: "2026-09-12T06:02:00Z" },
  ],
};

describe("OperationsTripPanel", () => {
  it("shows direction, concurrent trips, and per-stop delay progression", async () => {
    const selectTrip = vi.fn();
    render(<OperationsTripPanel
      routeName="B1 新町線"
      activeRoutes={[]}
      directions={[{ key: "direction:1", label: "新町", trips }]}
      selectedDirection="direction:1"
      trips={trips}
      selectedTripId="T1"
      progress={progress}
      progressLoading={false}
      onSelectDirection={() => {}}
      onSelectRoute={() => {}}
      onSelectTrip={selectTrip}
      t={t}
    />);

    expect(screen.getByRole("tab", { name: /新町/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("中央病院")).toBeInTheDocument();
    expect(screen.getAllByText("市役所前").length).toBeGreaterThan(0);
    expect(screen.getByRole("img", { name: "operations.trip_panel.chart_label" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /15:20/ }));
    expect(selectTrip).toHaveBeenCalledWith(expect.objectContaining({ trip_id: "T2" }));
  });
});
