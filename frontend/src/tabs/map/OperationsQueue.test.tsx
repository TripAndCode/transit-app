import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { TFunction } from "i18next";
import type { LiveTrip, RouteSummary } from "../../api/types";
import { OperationsQueue } from "./OperationsQueue";

const t = ((key: string, values?: Record<string, unknown>) => {
  if (values?.stop) return `${key}: ${values.stop}`;
  return key;
}) as TFunction;

function summary(overrides: Partial<RouteSummary> = {}): RouteSummary {
  return {
    route_code: "12",
    service_type: "weekday",
    avg_delay_sec: 420,
    worst_delay_sec: 600,
    trips_observed: 4,
    samples: 20,
    last_seen_at: "2026-09-11T04:00:00Z",
    baseline_avg_sec: 60,
    baseline_p90_sec: 180,
    baseline_samples: 100,
    deviation_sec: 360,
    bucket: "anomaly",
    low_confidence: false,
    has_baseline: true,
    ...overrides,
  };
}

const trip: LiveTrip = {
  trip_id: "trip-12",
  route_code: "12",
  service_type: "weekday",
  scheduled_time: "13:00:00",
  dep_delay: 420,
  captured_at: "2026-09-11T04:00:00Z",
  stop_id: "stop-7",
  stop_sequence: 7,
  stop_name: "高須台中央",
  stop_lat: 34.4,
  stop_lon: 132.4,
  headsign: "己斐上",
};

describe("OperationsQueue", () => {
  it("connects an anomalous route to its latest reported stop and actions", async () => {
    const select = vi.fn();
    const open = vi.fn();
    render(
      <OperationsQueue
        routes={[summary()]}
        trips={[trip]}
        selectedRoute="12"
        formatRoute={(code) => `Route ${code}`}
        onSelectRoute={select}
        onOpenRoute={open}
        t={t}
      />,
    );

    expect(screen.getByText("Route 12")).toBeInTheDocument();
    expect(screen.getByText("operations.queue.reported_stop: 高須台中央")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /operations.queue.show_on_map/ }));
    expect(select).toHaveBeenCalledWith("12");
    await userEvent.click(screen.getByRole("button", { name: /operations.queue.open_trip/ }));
    expect(open).toHaveBeenCalledWith(expect.objectContaining({ route_code: "12" }));
  });

  it("shows a calm empty state when no active route needs attention", () => {
    render(
      <OperationsQueue
        routes={[summary({ bucket: "normal", deviation_sec: 0 })]}
        trips={[trip]}
        selectedRoute={null}
        formatRoute={(code) => code}
        onSelectRoute={() => {}}
        onOpenRoute={() => {}}
        t={t}
      />,
    );

    expect(screen.getByText("operations.queue.clear_title")).toBeInTheDocument();
    expect(screen.getByText("operations.queue.normal_routes")).toBeInTheDocument();
  });
});
