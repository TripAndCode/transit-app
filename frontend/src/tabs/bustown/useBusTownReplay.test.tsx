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
});
