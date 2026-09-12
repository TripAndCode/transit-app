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

  it("counts no_baseline routes alongside normal ones instead of dropping them", () => {
    render(
      <OperationsQueue
        routes={[
          summary({ bucket: "normal", deviation_sec: 0 }),
          summary({ route_code: "13", bucket: "no_baseline", has_baseline: false, deviation_sec: null }),
        ]}
        trips={[trip]}
        selectedRoute={null}
        formatRoute={(code) => code}
        onSelectRoute={() => {}}
        onOpenRoute={() => {}}
        t={((key: string, values?: Record<string, unknown>) =>
          values ? `${key}:${JSON.stringify(values)}` : key) as TFunction}
      />,
    );

    expect(screen.getByText('operations.queue.normal_routes:{"count":2}')).toBeInTheDocument();
  });

  it("keeps long priority groups compact until the operator expands them", async () => {
    const routes = Array.from({ length: 7 }, (_, index) => summary({ route_code: `R${index + 1}` }));
    render(
      <OperationsQueue
        routes={routes}
        trips={[]}
        selectedRoute={null}
        formatRoute={(code) => code}
        onSelectRoute={() => {}}
        onOpenRoute={() => {}}
        t={t}
      />,
    );

    expect(screen.getAllByText("R5")).toHaveLength(2);
    expect(screen.queryByText("R6")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "operations.queue.show_more" }));
    expect(screen.getAllByText("R6")).toHaveLength(2);
    expect(screen.getAllByText("R7")).toHaveLength(2);
  });

  it("keeps a selected route visible when it ranks below the initial five", () => {
    const routes = Array.from({ length: 7 }, (_, index) => summary({ route_code: `R${index + 1}` }));
    render(
      <OperationsQueue
        routes={routes}
        trips={[]}
        selectedRoute="R7"
        formatRoute={(code) => code}
        onSelectRoute={() => {}}
        onOpenRoute={() => {}}
        t={t}
      />,
    );

    expect(screen.getAllByText("R7")).toHaveLength(2);
    expect(screen.getAllByText("R5")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "operations.queue.show_more" })).not.toBeInTheDocument();
  });
});
