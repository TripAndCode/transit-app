import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
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

/** Force the phone/desktop branch `useMediaQuery` reads, so the narrow layout
 *  can be asserted in jsdom (which reports no viewport width of its own). */
function mockViewport(narrow: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
    matches: narrow,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList);
}

afterEach(() => {
  vi.restoreAllMocks();
});

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
    // Scoped to the drawing: the same stop names also appear in the tabular
    // reading of the chart that follows it.
    const { container } = show();
    const labels = [...container.querySelectorAll("svg.marey__chart text")].map((node) => node.textContent);
    for (const stop of AXIS) expect(labels).toContain(stop.stop_name);
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

describe("MareyDiagram keyboard and screen-reader access", () => {
  it("puts every trip in the tab order with a label naming its route of travel and times", () => {
    const { container } = show();
    const groups = [...container.querySelectorAll("[data-trip-id]")];
    expect(groups.every((g) => g.getAttribute("tabindex") === "0")).toBe(true);
    const label = groups[2].getAttribute("aria-label") ?? "";
    expect(label).toContain("08:00");
    expect(label).toContain("Station");
    expect(label).toContain("Terminus");
    expect(label).toContain("15.0");
  });

  it("reads out the focused trip and mirrors the hover fade", () => {
    const { container } = show();
    const groups = [...container.querySelectorAll("[data-trip-id]")];

    fireEvent.focus(groups[2]);
    expect(screen.getByTestId("marey-readout")).toHaveTextContent("08:00");
    expect(groups[0].getAttribute("opacity")).toBe(String(MUTED_OPACITY));

    fireEvent.blur(groups[2]);
    expect(groups.every((g) => g.getAttribute("opacity") === "1")).toBe(true);
  });

  it("leaves a still-hovered trip highlighted after a different trip is blurred", () => {
    const { container } = show();
    const groups = [...container.querySelectorAll("[data-trip-id]")];

    fireEvent.mouseEnter(groups[1].querySelector(".marey-trip__hit")!);
    fireEvent.focus(groups[2]);
    // Tabbing away raises no `mouseleave`, so the pointer is still on group 1.
    fireEvent.blur(groups[2]);

    expect(groups[1].getAttribute("opacity")).toBe("1");
    expect(groups[0].getAttribute("opacity")).toBe(String(MUTED_OPACITY));
  });

  it("announces the readout politely so a change on focus is spoken", () => {
    show();
    expect(screen.getByTestId("marey-readout")).toHaveAttribute("aria-live", "polite");
  });

  it("gives the chart an image role and a summary label", () => {
    const { container } = show();
    const svg = container.querySelector("svg.marey__chart")!;
    expect(svg.getAttribute("role")).toBe("img");
    expect(svg.getAttribute("aria-label")).toContain("3");
  });

  it("follows the chart with one table row per drawn trip", () => {
    show();
    const table = screen.getByTestId("marey-table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(within(rows[2]).getByText("08:00")).toBeInTheDocument();
    expect(within(rows[2]).getByText("15.0")).toBeInTheDocument();
  });
});

describe("MareyDiagram on a narrow viewport", () => {
  it("shows the table instead of the chart, behind a toggle", async () => {
    mockViewport(true);
    const { container } = show();
    expect(container.querySelector("svg.marey__chart")).toBeNull();
    expect(screen.getByTestId("marey-table")).toBeVisible();

    const toggle = screen.getByRole("button", { name: "Show diagram" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);

    expect(container.querySelector("svg.marey__chart")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Hide diagram" })).toHaveAttribute("aria-expanded", "true");
  });

  it("keeps the chart and the hidden table on a wide viewport", () => {
    mockViewport(false);
    const { container } = show();
    expect(container.querySelector("svg.marey__chart")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Show diagram" })).toBeNull();
    expect(screen.getByTestId("marey-table").className).toContain("marey__table--hidden");
  });
});
