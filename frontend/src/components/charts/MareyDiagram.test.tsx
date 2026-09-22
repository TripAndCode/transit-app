import type { ComponentProps } from "react";
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "../../i18n";
import { MareyDiagram } from "./MareyDiagram";
import { MUTED_OPACITY, formatClock, type MareyStop } from "./mareyLayout";
import type { RouteTrip } from "../../api/types";

const AXIS: MareyStop[] = [
  { stop_sequence: 1, stop_name: "Station" },
  { stop_sequence: 2, stop_name: "Centre" },
  { stop_sequence: 3, stop_name: "Terminus" },
];

function trip(id: string, departSec: number, delays: number[]): RouteTrip {
  return {
    trip_id: id,
    scheduled_time: formatClock(departSec),
    headsign: null,
    avg_delay_sec: Math.round(delays.reduce((a, b) => a + b, 0) / delays.length),
    samples: delays.length,
    stops: delays.map((delay_sec, i) => ({
      stop_id: `S${i + 1}`,
      stop_sequence: i + 1,
      scheduled_sec: departSec + i * 300,
      observed_sec: departSec + i * 300 + delay_sec,
      delay_sec,
    })),
  };
}

const THREE_TRIPS = [
  trip("T1", 7 * 3600, [0, 60, 120]),
  trip("T2", 7 * 3600 + 1800, [120, 300, 600]),
  trip("T3", 8 * 3600, [60, 660, 900]),
];

function show(props: Partial<ComponentProps<typeof MareyDiagram>> = {}) {
  return render(<MareyDiagram trips={THREE_TRIPS} axis={AXIS} band="all" {...props} />);
}

describe("MareyDiagram", () => {
  it("draws one polyline per trip", () => {
    const { container } = show();
    expect(container.querySelectorAll("[data-trip-id]")).toHaveLength(3);
    expect([...container.querySelectorAll("[data-trip-id]")].map((g) => g.getAttribute("data-trip-id"))).toEqual([
      "T1",
      "T2",
      "T3",
    ]);
  });

  it("colours each leg by the delay at the stop it arrives at", () => {
    const { container } = show({ trips: [trip("T1", 7 * 3600, [0, 660])] });
    const legs = container.querySelectorAll("[data-trip-id] line");
    expect(legs).toHaveLength(1);
    expect(legs[0].getAttribute("stroke")).toBe("var(--delay-severe)");
  });

  it("labels every stop on the y axis", () => {
    show();
    for (const stop of AXIS) expect(screen.getByText(stop.stop_name)).toBeInTheDocument();
  });

  it("fades the other trips while one is hovered", () => {
    const { container } = show();
    const groups = [...container.querySelectorAll("[data-trip-id]")];
    expect(groups.every((g) => g.getAttribute("opacity") === "1")).toBe(true);

    fireEvent.mouseEnter(groups[1].querySelector(".marey-trip__hit")!);
    expect(groups[1].getAttribute("opacity")).toBe("1");
    expect(groups[0].getAttribute("opacity")).toBe(String(MUTED_OPACITY));
    expect(groups[2].getAttribute("opacity")).toBe(String(MUTED_OPACITY));

    fireEvent.mouseLeave(groups[1].querySelector(".marey-trip__hit")!);
    expect(groups.every((g) => g.getAttribute("opacity") === "1")).toBe(true);
  });

  it("reads out the hovered trip's departure and terminal delay", () => {
    const { container } = show();
    fireEvent.mouseEnter(container.querySelectorAll(".marey-trip__hit")[2]);
    const readout = screen.getByTestId("marey-readout");
    expect(readout).toHaveTextContent("08:00");
    expect(readout).toHaveTextContent("15.0");
  });

  it("shades the worst hour of the drawn window", () => {
    const { container } = show();
    const peak = container.querySelector("[data-testid='marey-peak']");
    expect(peak).not.toBeNull();
  });

  it("draws the previous week behind the current trips, unhighlighted", () => {
    const { container } = show({ previousTrips: [trip("P1", 7 * 3600 + 300, [0, 60, 120])] });
    const ghosts = container.querySelectorAll("[data-ghost='true']");
    expect(ghosts).toHaveLength(1);
    expect(ghosts[0].querySelector("line")?.getAttribute("stroke")).toBe("var(--text-tertiary)");
    // No hit target: the comparison is context, not something to interrogate.
    expect(ghosts[0].querySelector(".marey-trip__hit")).toBeNull();
  });

  it("draws nothing but says so when no trip ran in the window", () => {
    const { container } = show({ trips: [trip("NIGHT", 23 * 3600, [60, 60, 60])] });
    expect(container.querySelectorAll("[data-trip-id]")).toHaveLength(0);
    expect(screen.getByText("No trips ran in this window")).toBeInTheDocument();
  });

  it("says when the payload was capped", () => {
    show({ truncated: true });
    expect(screen.getByTestId("marey-truncated")).toBeInTheDocument();
  });

  it("stays silent about capping when nothing was dropped", () => {
    show();
    expect(screen.queryByTestId("marey-truncated")).toBeNull();
  });
});
