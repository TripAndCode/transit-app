import { describe, expect, it } from "vitest";
import {
  DEFAULT_MAREY_WINDOW,
  formatClock,
  peakWindow,
  ribbonSegments,
  segmentColor,
  seqToY,
  timeToX,
  timeWindowForBand,
  tripDeparture,
  tripTerminalDelay,
  tripsInWindow,
} from "./mareyLayout";
import type { MareyStop } from "./mareyLayout";
import type { RouteTrip } from "../../api/types";

const PLOT = { left: 100, top: 20, width: 600, height: 300 };

const AXIS: MareyStop[] = [
  { stop_sequence: 1, stop_name: "駅前" },
  { stop_sequence: 2, stop_name: "中央" },
  { stop_sequence: 3, stop_name: "終点" },
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

describe("timeWindowForBand", () => {
  it("uses the band's own clock range", () => {
    expect(timeWindowForBand("morning")).toEqual({ startSec: 5 * 3600, endSec: 9 * 3600 });
    expect(timeWindowForBand("evening")).toEqual({ startSec: 17 * 3600, endSec: 20 * 3600 });
    expect(timeWindowForBand("late_night")).toEqual({ startSec: 0, endSec: 5 * 3600 });
  });

  it("falls back to the commute window when no band is selected", () => {
    expect(timeWindowForBand("all")).toEqual(DEFAULT_MAREY_WINDOW);
    expect(DEFAULT_MAREY_WINDOW).toEqual({ startSec: 6 * 3600, endSec: 10 * 3600 });
  });
});

describe("timeToX", () => {
  it("maps the window's ends onto the plot's ends", () => {
    const w = { startSec: 0, endSec: 100 };
    expect(timeToX(0, w, PLOT)).toBe(100);
    expect(timeToX(100, w, PLOT)).toBe(700);
    expect(timeToX(50, w, PLOT)).toBe(400);
  });

  it("keeps a time outside the window outside the plot rather than clamping it", () => {
    // The renderer clips; silently clamping would pile every early trip onto
    // the left edge and invent a vertical line that no bus ran.
    const w = { startSec: 0, endSec: 100 };
    expect(timeToX(-50, w, PLOT)).toBeLessThan(PLOT.left);
    expect(timeToX(150, w, PLOT)).toBeGreaterThan(PLOT.left + PLOT.width);
  });

  it("does not divide by zero on a degenerate window", () => {
    expect(Number.isFinite(timeToX(10, { startSec: 10, endSec: 10 }, PLOT))).toBe(true);
  });
});

describe("seqToY", () => {
  it("spreads the axis evenly from the plot's top to its bottom", () => {
    expect(seqToY(1, AXIS, PLOT)).toBe(20);
    expect(seqToY(2, AXIS, PLOT)).toBe(170);
    expect(seqToY(3, AXIS, PLOT)).toBe(320);
  });

  it("returns null for a stop the route's axis does not contain", () => {
    // A shape variant can report a stop_sequence the representative path has
    // no row for; it has no y and must be skipped, not drawn at y=0.
    expect(seqToY(99, AXIS, PLOT)).toBeNull();
  });

  it("puts a single-stop axis at the top of the plot", () => {
    expect(seqToY(1, [AXIS[0]], PLOT)).toBe(20);
  });
});

describe("segmentColor", () => {
  it("reads the shared delay ramp in minutes, not seconds", () => {
    expect(segmentColor(0)).toBe("#2EA87A");
    expect(segmentColor(120)).toBe("#C99A2E");
    expect(segmentColor(240)).toBe("#D4622A");
    expect(segmentColor(600)).toBe("var(--delay-severe)");
  });

  it("treats an early arrival as on time", () => {
    expect(segmentColor(-180)).toBe("#2EA87A");
  });
});

describe("trip summaries", () => {
  it("reads the departure off the first stop that has a scheduled time", () => {
    expect(tripDeparture(trip("A", 7 * 3600, [0, 60]))).toBe(7 * 3600);
  });

  it("reads the terminal delay off the last stop", () => {
    expect(tripTerminalDelay(trip("A", 7 * 3600, [0, 60, 300]))).toBe(300);
  });

  it("has no departure and no terminal delay for a trip with no placed stops", () => {
    const bare: RouteTrip = { ...trip("A", 0, [0]), stops: [] };
    expect(tripDeparture(bare)).toBeNull();
    expect(tripTerminalDelay(bare)).toBeNull();
  });
});

describe("tripsInWindow", () => {
  const viewWindow = { startSec: 6 * 3600, endSec: 10 * 3600 };

  it("keeps a trip departing inside the window", () => {
    expect(tripsInWindow([trip("A", 7 * 3600, [0])], viewWindow).map((t) => t.trip_id)).toEqual(["A"]);
  });

  it("drops a trip departing outside the window", () => {
    expect(tripsInWindow([trip("A", 13 * 3600, [0])], viewWindow)).toEqual([]);
  });

  it("drops a trip with no placeable departure", () => {
    const bare: RouteTrip = { ...trip("A", 0, [0]), stops: [] };
    expect(tripsInWindow([bare], viewWindow)).toEqual([]);
  });
});

describe("peakWindow", () => {
  const viewWindow = { startSec: 6 * 3600, endSec: 10 * 3600 };

  it("picks the hour whose trips lost the most time", () => {
    const trips = [
      trip("A", 6 * 3600, [30, 30, 30]),
      trip("B", 8 * 3600, [600, 660, 720]),
      trip("C", 8 * 3600 + 1800, [540, 600, 600]),
      trip("D", 9 * 3600, [60, 60, 60]),
    ];
    expect(peakWindow(trips, viewWindow)).toEqual({ startSec: 8 * 3600, endSec: 9 * 3600 });
  });

  it("returns null when no hour has enough observations to call a peak", () => {
    expect(peakWindow([], viewWindow)).toBeNull();
    expect(peakWindow([trip("A", 7 * 3600, [60])], viewWindow)).toBeNull();
  });

  it("ignores trips outside the drawn window", () => {
    const trips = [
      trip("A", 7 * 3600, [60, 60, 60]),
      trip("LATE", 22 * 3600, [900, 900, 900]),
    ];
    expect(peakWindow(trips, viewWindow)).toEqual({ startSec: 7 * 3600, endSec: 8 * 3600 });
  });
});

describe("ribbonSegments", () => {
  const viewWindow = { startSec: 8 * 3600, endSec: 9 * 3600 };

  it("averages each stop's delay across the trips in the window", () => {
    const trips = [trip("A", 8 * 3600, [0, 120, 600]), trip("B", 8 * 3600 + 600, [60, 180, 660])];
    expect(ribbonSegments(trips, AXIS, viewWindow)).toEqual([
      { stop_sequence: 1, stop_name: "駅前", delay_sec: 30, samples: 2 },
      { stop_sequence: 2, stop_name: "中央", delay_sec: 150, samples: 2 },
      { stop_sequence: 3, stop_name: "終点", delay_sec: 630, samples: 2 },
    ]);
  });

  it("keeps an unobserved stop in the ribbon with no delay rather than dropping it", () => {
    // The ribbon is the route's geography; a missing stop must read as a gap,
    // not silently shorten the line.
    const partial: RouteTrip = { ...trip("A", 8 * 3600, [0, 120, 600]) };
    partial.stops = partial.stops.slice(0, 2);
    expect(ribbonSegments([partial], AXIS, viewWindow)[2]).toEqual({
      stop_sequence: 3,
      stop_name: "終点",
      delay_sec: null,
      samples: 0,
    });
  });

  it("is all gaps when nothing ran in the window", () => {
    expect(ribbonSegments([], AXIS, viewWindow).every((s) => s.delay_sec === null)).toBe(true);
  });
});

describe("formatClock", () => {
  it("renders a zero-padded 24-hour clock", () => {
    expect(formatClock(7 * 3600 + 5 * 60)).toBe("07:05");
    expect(formatClock(0)).toBe("00:00");
  });

  it("keeps a post-midnight continuation hour unwrapped", () => {
    // 25:30 is a real GTFS departure time; wrapping it to 01:30 would put the
    // trip at the wrong end of the axis.
    expect(formatClock(25 * 3600 + 30 * 60)).toBe("25:30");
  });
});
