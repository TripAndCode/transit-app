import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PlaybackRail } from "./PlaybackRail";
import type { PlaybackController } from "./useDayPlayback";
import type { TimelineFrame } from "../../api/types";

const t = ((key: string, opts?: Record<string, unknown>) =>
  opts ? `${key}:${Object.values(opts).join(",")}` : key) as unknown as Parameters<typeof PlaybackRail>[0]["t"];

function frames(count: number): TimelineFrame[] {
  return Array.from({ length: count }, (_, i) => ({
    t: `${String(5 + i).padStart(2, "0")}:00`,
    points: [{ stop_id: `S${i}`, stop_name: `S${i}`, lon: 140, lat: 40, avg_delay_min: i, samples: 5 }],
    mean_delay_min: i,
    samples: 5,
  }));
}

function controller(overrides: Partial<PlaybackController> = {}): PlaybackController {
  return {
    frames: frames(4),
    date: "2026-03-04",
    index: 1,
    playing: false,
    speed: 1,
    steppingOnly: false,
    loading: false,
    error: null,
    setIndex: vi.fn(),
    step: vi.fn(),
    toggle: vi.fn(),
    pause: vi.fn(),
    cycleSpeed: vi.fn(),
    ...overrides,
  };
}

function renderRail(overrides: Partial<PlaybackController> = {}) {
  const c = controller(overrides);
  render(<PlaybackRail controller={c} onExit={vi.fn()} t={t} />);
  return c;
}

describe("PlaybackRail", () => {
  it("shows the frame's clock time and the day it is playing", () => {
    renderRail();
    expect(screen.getByText("06:00")).toBeTruthy();
    expect(screen.getByText(/2026-03-04/)).toBeTruthy();
  });

  it("scrubs to the frame the slider is moved to", () => {
    const c = renderRail();
    const slider = screen.getByRole("slider");
    fireEvent.change(slider, { target: { value: "3" } });
    expect(c.setIndex).toHaveBeenCalledWith(3);
  });

  it("reports the slider's position as a clock time, not a frame number", () => {
    renderRail();
    expect(screen.getByRole("slider").getAttribute("aria-valuetext")).toBe("06:00");
  });

  it("steps one frame on ArrowRight and back on ArrowLeft", () => {
    const c = renderRail();
    const slider = screen.getByRole("slider");
    fireEvent.keyDown(slider, { key: "ArrowRight" });
    expect(c.step).toHaveBeenCalledWith(1);
    fireEvent.keyDown(slider, { key: "ArrowLeft" });
    expect(c.step).toHaveBeenCalledWith(-1);
  });

  it("toggles playback on space", () => {
    const c = renderRail();
    fireEvent.keyDown(screen.getByRole("slider"), { key: " " });
    expect(c.toggle).toHaveBeenCalledTimes(1);
  });

  it("leaves space to the browser when a button has focus, so it is not toggled twice", () => {
    const c = renderRail();
    fireEvent.keyDown(screen.getByRole("button", { name: "operations.playback.play" }), { key: " " });
    expect(c.toggle).not.toHaveBeenCalled();
  });

  it("switches the play control to pause while playing", async () => {
    const c = renderRail({ playing: true });
    await userEvent.click(screen.getByRole("button", { name: "operations.playback.pause" }));
    expect(c.toggle).toHaveBeenCalledTimes(1);
  });

  it("cycles the speed", async () => {
    const c = renderRail();
    await userEvent.click(screen.getByRole("button", { name: "operations.playback.speed:1" }));
    expect(c.cycleSpeed).toHaveBeenCalledTimes(1);
  });

  it("offers stepping instead of playback under reduced motion", () => {
    renderRail({ steppingOnly: true });
    expect(screen.queryByRole("button", { name: "operations.playback.play" })).toBeNull();
    expect(screen.getByRole("button", { name: "operations.playback.step_forward" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "operations.playback.step_back" })).toBeTruthy();
  });

  it("ignores space under reduced motion, where there is nothing to toggle", () => {
    const c = renderRail({ steppingOnly: true });
    fireEvent.keyDown(screen.getByRole("slider"), { key: " " });
    expect(c.toggle).not.toHaveBeenCalled();
  });

  it("still steps with the arrow keys under reduced motion", () => {
    const c = renderRail({ steppingOnly: true });
    fireEvent.keyDown(screen.getByRole("slider"), { key: "ArrowRight" });
    expect(c.step).toHaveBeenCalledWith(1);
  });

  it("paints one load-bar segment per frame", () => {
    renderRail();
    expect(screen.getAllByTestId("playback-load-segment")).toHaveLength(4);
  });

  it("says it is loading and offers no scrubber before the day arrives", () => {
    renderRail({ frames: [], loading: true, date: null });
    expect(screen.getByText("operations.playback.loading")).toBeTruthy();
    expect(screen.queryByRole("slider")).toBeNull();
  });

  it("leaves playback mode through its own control", async () => {
    const c = controller();
    const onExit = vi.fn();
    render(<PlaybackRail controller={c} onExit={onExit} t={t} />);
    await userEvent.click(screen.getByRole("button", { name: "operations.playback.exit" }));
    expect(onExit).toHaveBeenCalledTimes(1);
  });
});
