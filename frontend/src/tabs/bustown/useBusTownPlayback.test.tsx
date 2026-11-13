import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { useState } from "react";
import type { ReplayResponse } from "../../api/types";
import { buildBusTownModel, mergeReplayWindows, type BusTownModel } from "./busTownModel";
import { useBusTownPlayback } from "./useBusTownPlayback";

/** One trip gliding between two stops from `0` to `durationSec`, so
 *  `positionsAt` always has something to draw across the harness's span. */
function makeModel(durationSec: number): BusTownModel {
  const replayWindow: ReplayResponse = {
    date: "2026-01-05",
    from_hour: 4,
    to_hour: 7,
    available_dates: ["2026-01-05"],
    stops: { id: ["a", "b"], lat: [35.0, 35.1], lon: [139.0, 139.1] },
    routes: [],
    trips: [{ trip_id: "t1", route_code: null, stop_idx: [0, 1], t: [0, durationSec], delay: [0, 0] }],
  };
  return buildBusTownModel(mergeReplayWindows([replayWindow]));
}

type HarnessProps = {
  model: BusTownModel;
  maxSec: number;
  minSec?: number;
  speed?: number;
  autoplay?: boolean;
  startMounted?: boolean;
};

/** Mirrors `AsyncSection`: the `<canvas>` is absent on the first render and
 *  only appears once `mounted` flips, driven by the "mount" button — the
 *  same late-mount shape a loading skeleton's `AsyncSection` gives a real
 *  caller once its data arrives. */
function Harness({ model, maxSec, minSec = 0, speed = 600, autoplay = false, startMounted = true }: HarnessProps) {
  const [sec, setSec] = useState(minSec);
  const [mounted, setMounted] = useState(startMounted);
  const { playing, play, pause, canvasRef } = useBusTownPlayback({
    model,
    stopPoints: [],
    stopPairs: [],
    bounds: null,
    minSec,
    maxSec,
    speed,
    autoplay,
    sec,
    setSec,
  });
  return (
    <>
      {mounted && <canvas ref={canvasRef} aria-hidden="true" />}
      <button type="button" onClick={() => setMounted(true)}>
        mount
      </button>
      <button type="button" onClick={() => (playing ? pause() : play())}>
        {playing ? "playing" : "paused"}
      </button>
      <span data-testid="sec">{sec}</span>
    </>
  );
}

/** A driveable requestAnimationFrame: `step` runs the pending frame `ms`
 *  later and reports whether the loop asked for another. */
function driveFrames() {
  let pending: FrameRequestCallback | null = null;
  let now = performance.now();
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
    pending = cb;
    return 1;
  });
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {
    pending = null;
  });
  return {
    step(ms = 100) {
      const cb = pending;
      pending = null;
      now += ms;
      if (cb) act(() => cb(now));
      return pending !== null;
    },
  };
}

function makeFakeCtx() {
  return {
    clearRect: vi.fn(),
    fillRect: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    stroke: vi.fn(),
    arc: vi.fn(),
    fill: vi.fn(),
    setTransform: vi.fn(),
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 0,
    globalAlpha: 1,
  } as unknown as CanvasRenderingContext2D;
}

describe("useBusTownPlayback", () => {
  let ctx: CanvasRenderingContext2D;

  beforeEach(() => {
    ctx = makeFakeCtx();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("draws once a canvas that wasn't there at the first render mounts", () => {
    driveFrames();
    render(<Harness model={makeModel(300)} maxSec={300} startMounted={false} />);
    expect(ctx.clearRect).not.toHaveBeenCalled();
    act(() => screen.getByRole("button", { name: "mount" }).click());
    expect(ctx.clearRect).toHaveBeenCalledTimes(1);
  });

  it("advances the playhead and keeps drawing once play is pressed, after the canvas mounts late", () => {
    const frames = driveFrames();
    render(<Harness model={makeModel(300)} maxSec={300} startMounted={false} />);
    act(() => screen.getByRole("button", { name: "mount" }).click());
    const drawsAfterMount = vi.mocked(ctx.clearRect).mock.calls.length;

    act(() => screen.getByRole("button", { name: "paused" }).click());
    expect(screen.getByRole("button", { name: "playing" })).toBeInTheDocument();
    frames.step();
    frames.step();
    expect(Number(screen.getByTestId("sec").textContent)).toBeGreaterThan(0);
    expect(vi.mocked(ctx.clearRect).mock.calls.length).toBeGreaterThan(drawsAfterMount);
  });

  it("starts playing without pressing play when autoplay is true", () => {
    const frames = driveFrames();
    render(<Harness model={makeModel(300)} maxSec={300} autoplay />);
    expect(screen.getByRole("button", { name: "playing" })).toBeInTheDocument();
    frames.step();
    frames.step();
    expect(Number(screen.getByTestId("sec").textContent)).toBeGreaterThan(0);
  });

  it("sets playing to false once the playhead reaches the end", () => {
    const frames = driveFrames();
    render(<Harness model={makeModel(150)} maxSec={150} autoplay />);
    frames.step();
    frames.step();
    frames.step();
    expect(screen.getByTestId("sec")).toHaveTextContent("150");
    expect(screen.getByRole("button", { name: "paused" })).toBeInTheDocument();
    expect(frames.step()).toBe(false);
  });

  it("restarts from the start of the day when play is pressed again at the end", () => {
    const frames = driveFrames();
    render(<Harness model={makeModel(150)} maxSec={150} autoplay />);
    frames.step();
    frames.step();
    frames.step();
    expect(screen.getByRole("button", { name: "paused" })).toBeInTheDocument();

    act(() => screen.getByRole("button", { name: "paused" }).click());
    expect(screen.getByTestId("sec")).toHaveTextContent("0");
    expect(screen.getByRole("button", { name: "playing" })).toBeInTheDocument();
    frames.step();
    expect(screen.getByTestId("sec")).toHaveTextContent("60");
  });
});
