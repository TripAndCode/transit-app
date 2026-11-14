import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReplayResponse } from "../../api/types";
import { desiredWindowKeys } from "./busTownModel";
import { useBusTownReplay } from "./useBusTownReplay";

const AGENCY = 1;
const PLAYHEAD = 5 * 3600;
const SPEED = 60;

function replayWindow(fromHour: number): ReplayResponse {
  return {
    date: "2026-01-05",
    from_hour: fromHour,
    to_hour: fromHour + 3,
    available_dates: ["2026-01-05"],
    departures_by_hour: [],
    stops: { id: ["a", "b"], lat: [35.0, 35.1], lon: [139.0, 139.1] },
    routes: [{ code: "R1", name: "Route 1", color: null }],
    trips: [{ trip_id: `t${fromHour}`, route_code: "R1", stop_idx: [0, 1], t: [fromHour * 3600, fromHour * 3600 + 600], delay: [0, 60] }],
  };
}

/** Every window the hook asks for is already cached, so nothing fetches. */
function seededClient(): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const day = replayWindow(4);
  client.setQueryData(["bus-town-replay", AGENCY, null, null, null], day);
  for (const key of desiredWindowKeys(day.date!, day.from_hour!, PLAYHEAD, SPEED)) {
    client.setQueryData(["bus-town-replay", AGENCY, key.date, key.fromHour, key.toHour], replayWindow(key.fromHour));
  }
  return client;
}

describe("useBusTownReplay", () => {
  it("keeps the built scene across a re-render whose query data did not change", () => {
    const client = seededClient();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, rerender } = renderHook(() => useBusTownReplay(AGENCY, null, PLAYHEAD, SPEED), { wrapper });
    const first = result.current;
    expect(first.model.size).toBeGreaterThan(0);

    rerender();

    expect(result.current.model).toBe(first.model);
    expect(result.current.trips).toBe(first.trips);
    expect(result.current.stopPoints).toBe(first.stopPoints);
    expect(result.current.stopPairs).toBe(first.stopPairs);
  });

  it("keeps the built scene across a playhead advance that stays in the same window", () => {
    const client = seededClient();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, rerender } = renderHook(({ sec }: { sec: number }) => useBusTownReplay(AGENCY, null, sec, SPEED), {
      wrapper,
      initialProps: { sec: PLAYHEAD },
    });
    const first = result.current;
    expect(first.model.size).toBeGreaterThan(0);

    rerender({ sec: PLAYHEAD + 60 });

    expect(result.current.model).toBe(first.model);
    expect(result.current.trips).toBe(first.trips);
    expect(result.current.stopPoints).toBe(first.stopPoints);
    expect(result.current.stopPairs).toBe(first.stopPairs);
  });

  it("fetches the window that covers the clamped playhead after a switch to a later-starting day", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const day = replayWindow(7);
    client.setQueryData(["bus-town-replay", AGENCY, null, null, null], day);
    for (const fromHour of [1, 4, 7]) {
      client.setQueryData(["bus-town-replay", AGENCY, day.date, fromHour, fromHour + 3], replayWindow(fromHour));
    }
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useBusTownReplay(AGENCY, null, 2 * 3600, SPEED), { wrapper });

    expect(result.current.playhead).toBe(4 * 3600);
    const covering = client.getQueryCache().find({ queryKey: ["bus-town-replay", AGENCY, day.date, 4, 7], exact: true });
    expect(covering?.getObserversCount()).toBeGreaterThan(0);
  });

  it("exposes the picked day's own hourly totals", () => {
    const client = seededClient();
    const day = client.getQueryData<ReplayResponse>(["bus-town-replay", AGENCY, null, null, null])!;
    client.setQueryData(["bus-town-replay", AGENCY, null, null, null], { ...day, departures_by_hour: [{ hour: 4, departures: 9 }] });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useBusTownReplay(AGENCY, null, PLAYHEAD, SPEED), { wrapper });

    expect(result.current.departuresByHour).toEqual([{ hour: 4, departures: 9 }]);
  });

  it("starts the playhead one minute before the first departure on a cold load, not the scrubber's own start", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const day: ReplayResponse = {
      ...replayWindow(4),
      trips: [{ trip_id: "t1", route_code: "R1", stop_idx: [0, 1], t: [5 * 3600 + 42 * 60 + 30, 5 * 3600 + 50 * 60], delay: [0, 0] }],
    };
    client.setQueryData(["bus-town-replay", AGENCY, null, null, null], day);
    for (const key of desiredWindowKeys(day.date!, day.from_hour!, 5 * 3600 + 41 * 60, SPEED)) {
      client.setQueryData(["bus-town-replay", AGENCY, key.date, key.fromHour, key.toHour], day);
    }
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useBusTownReplay(AGENCY, null, null, SPEED), { wrapper });

    expect(result.current.playhead).toBe(5 * 3600 + 41 * 60);
  });

  it("re-resolves a null playhead to the newly picked day's own first departure", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const dayA = replayWindow(4);
    const dayB: ReplayResponse = {
      ...replayWindow(4),
      date: "2026-01-06",
      trips: [{ trip_id: "t1", route_code: "R1", stop_idx: [0, 1], t: [6 * 3600 + 10 * 60, 6 * 3600 + 20 * 60], delay: [0, 0] }],
    };
    client.setQueryData(["bus-town-replay", AGENCY, null, null, null], dayA);
    for (const key of desiredWindowKeys(dayA.date!, dayA.from_hour!, PLAYHEAD, SPEED)) {
      client.setQueryData(["bus-town-replay", AGENCY, key.date, key.fromHour, key.toHour], dayA);
    }
    client.setQueryData(["bus-town-replay", AGENCY, "2026-01-06", null, null], dayB);
    for (const key of desiredWindowKeys(dayB.date!, dayB.from_hour!, 6 * 3600 + 9 * 60, SPEED)) {
      client.setQueryData(["bus-town-replay", AGENCY, key.date, key.fromHour, key.toHour], dayB);
    }
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    const { result, rerender } = renderHook(
      ({ date, sec }: { date: string | null; sec: number | null }) => useBusTownReplay(AGENCY, date, sec, SPEED),
      { wrapper, initialProps: { date: null as string | null, sec: PLAYHEAD as number | null } },
    );
    expect(result.current.playhead).toBe(PLAYHEAD);

    rerender({ date: "2026-01-06", sec: null });

    expect(result.current.playhead).toBe(6 * 3600 + 9 * 60);
  });

  it("never falls back to the default day's hourly totals for a picked day that hasn't loaded yet, unlike availableDates", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const defaultDay = { ...replayWindow(4), departures_by_hour: [{ hour: 4, departures: 9 }] };
    client.setQueryData(["bus-town-replay", AGENCY, null, null, null], defaultDay);
    // "2026-01-06" is deliberately left uncached, so dayQuery.data is
    // undefined for it — the one state where availableDates still falls
    // back to the default day's query but departuresByHour must not.
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useBusTownReplay(AGENCY, "2026-01-06", PLAYHEAD, SPEED), { wrapper });

    expect(result.current.loaded).toBe(false);
    expect(result.current.availableDates).toEqual(defaultDay.available_dates);
    expect(result.current.departuresByHour).toEqual([]);
  });
});
