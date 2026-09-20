import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { LiveTrip, LiveTripProgressResponse } from "../../api/types";
import { InspectCard } from "./InspectCard";

/** Echoes the key, with interpolation spelled out for the keys under test. */
const t = ((key: string, opts?: Record<string, unknown>) => {
  if (key === "operations.inspect.segment") return `${opts?.from} to ${opts?.to}`;
  if (key === "operations.inspect.vehicles") return `${opts?.vehicles} running`;
  return key;
}) as unknown as Parameters<typeof InspectCard>[0]["t"];

const TRIP: LiveTrip = {
  trip_id: "trip-1",
  route_code: "42",
  service_type: "weekday",
  scheduled_time: "08:10:00",
  dep_delay: 192,
  captured_at: "2026-09-19T08:40:00+09:00",
  stop_id: "B",
  stop_sequence: 2,
  stop_name: "Saigawa Bridge",
  stop_lat: 40.8,
  stop_lon: 140.7,
  headsign: "Yuhidera",
  direction_id: 0,
};

function progressStop(overrides: Partial<LiveTripProgressResponse["stops"][number]>) {
  return {
    stop_sequence: 1,
    stop_id: "A",
    stop_name: "Kanazawa Port",
    stop_lat: null,
    stop_lon: null,
    scheduled_time: "07:10:00",
    dep_delay: 60,
    reported_at: "2026-09-19T07:12:00+09:00",
    ...overrides,
  };
}

const PROGRESS: LiveTripProgressResponse = {
  trip_id: "trip-1",
  route_code: "42",
  headsign: "Yuhidera",
  direction_id: 0,
  latest_captured_at: "2026-09-19T09:05:00+09:00",
  stops: [
    progressStop({}),
    progressStop({
      stop_sequence: 2,
      stop_id: "B",
      stop_name: "Saigawa Bridge",
      dep_delay: 120,
      reported_at: "2026-09-19T08:02:00+09:00",
    }),
    progressStop({
      stop_sequence: 3,
      stop_id: "C",
      stop_name: "Nomachi",
      dep_delay: 540,
      reported_at: "2026-09-19T09:05:00+09:00",
    }),
  ],
};

function renderCard(overrides: Partial<Parameters<typeof InspectCard>[0]> = {}) {
  const props = {
    trip: TRIP,
    routeName: "Kanazawa Port - Yuhidera",
    vehicles: 3,
    progress: PROGRESS,
    pinned: true,
    onPin: vi.fn(),
    onUnpin: vi.fn(),
    t,
    ...overrides,
  };
  render(<InspectCard {...props} />);
  return props;
}

describe("InspectCard", () => {
  it("leads with the route badge, name, headsign and the current delay", () => {
    renderCard();

    expect(screen.getByText("42")).toBeTruthy();
    expect(screen.getByText("Kanazawa Port - Yuhidera")).toBeTruthy();
    expect(screen.getByText("Yuhidera")).toBeTruthy();
    expect(screen.getByText("+3.2")).toBeTruthy();
  });

  it("signs an early trip once, from the value itself", () => {
    renderCard({ trip: { ...TRIP, dep_delay: -90 } });
    expect(screen.getByText("-1.5")).toBeTruthy();
  });

  it("names the segment where the delay grows most", () => {
    renderCard();
    expect(screen.getByText("Saigawa Bridge to Nomachi")).toBeTruthy();
  });

  it("omits the worst segment when the trip never loses time", () => {
    renderCard({
      progress: {
        ...PROGRESS,
        stops: PROGRESS.stops.map((stop, index) => ({ ...stop, dep_delay: 300 - index * 60 })),
      },
    });
    expect(screen.queryByText("operations.inspect.worst_segment")).toBeNull();
  });

  it("counts the vehicles running the route and draws the hourly trend", () => {
    renderCard();

    expect(screen.getByText("3 running")).toBeTruthy();
    expect(screen.getByTestId("inspect-sparkline")).toBeTruthy();
  });

  it("shows only the headline while previewing a hovered vehicle", () => {
    renderCard({ pinned: false });

    expect(screen.getByText("+3.2")).toBeTruthy();
    expect(screen.queryByTestId("inspect-sparkline")).toBeNull();
    expect(screen.queryByText("3 running")).toBeNull();
    expect(screen.queryByText("Saigawa Bridge to Nomachi")).toBeNull();
  });

  it("pins a previewed trip and unpins a pinned one", async () => {
    const preview = renderCard({ pinned: false });
    await userEvent.click(screen.getByRole("button", { name: "operations.inspect.pin" }));
    expect(preview.onPin).toHaveBeenCalledTimes(1);
    expect(preview.onUnpin).not.toHaveBeenCalled();

    screen.getByRole("button", { name: "operations.inspect.pin" }).blur();
    const pinnedCard = renderCard({ pinned: true });
    await userEvent.click(screen.getByRole("button", { name: "operations.inspect.unpin" }));
    expect(pinnedCard.onUnpin).toHaveBeenCalledTimes(1);
  });

  it("stays standing with no progress data yet", () => {
    renderCard({ progress: undefined });

    expect(screen.getByText("+3.2")).toBeTruthy();
    expect(screen.queryByTestId("inspect-sparkline")).toBeNull();
  });
});
