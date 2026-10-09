import { describe, expect, it } from "vitest";
import {
  FRAME_MS,
  GHOST_OPACITY,
  clampFrameIndex,
  frameIndexAt,
  stepFrameIndex,
  timelineFeatures,
} from "./playbackFrames";
import type { TimelineFrame } from "../../api/types";

function frame(t: string, stops: [string, number][]): TimelineFrame {
  const points = stops.map(([stop_id, avg_delay_min], i) => ({
    stop_id,
    stop_name: stop_id,
    lon: 140 + i / 100,
    lat: 40 + i / 100,
    avg_delay_min,
    samples: 5,
  }));
  return {
    t,
    points,
    mean_delay_min: points.length ? points[0].avg_delay_min : null,
    samples: points.length * 5,
  };
}

const FRAMES = [
  frame("05:00", [["A", 1]]),
  frame("06:00", [["A", 2], ["B", 3]]),
  frame("07:00", [["B", 4]]),
  frame("08:00", []),
];

describe("frameIndexAt", () => {
  it("holds frame 0 for the first frame's worth of time", () => {
    expect(frameIndexAt(0, FRAMES.length, 1)).toBe(0);
    expect(frameIndexAt(FRAME_MS - 1, FRAMES.length, 1)).toBe(0);
  });

  it("advances one frame per FRAME_MS at 1x", () => {
    expect(frameIndexAt(FRAME_MS, FRAMES.length, 1)).toBe(1);
    expect(frameIndexAt(FRAME_MS * 2.5, FRAMES.length, 1)).toBe(2);
  });

  it("advances twice as fast at 2x", () => {
    expect(frameIndexAt(FRAME_MS, FRAMES.length, 2)).toBe(2);
    expect(frameIndexAt(FRAME_MS / 2, FRAMES.length, 2)).toBe(1);
  });

  it("loops back to the start rather than stalling on the last frame", () => {
    expect(frameIndexAt(FRAME_MS * FRAMES.length, FRAMES.length, 1)).toBe(0);
    expect(frameIndexAt(FRAME_MS * (FRAMES.length + 1), FRAMES.length, 1)).toBe(1);
  });

  it("is 0 when there is nothing to play", () => {
    expect(frameIndexAt(FRAME_MS * 3, 0, 1)).toBe(0);
  });
});

describe("stepFrameIndex", () => {
  it("steps forward and back within the range", () => {
    expect(stepFrameIndex(1, 1, 4)).toBe(2);
    expect(stepFrameIndex(1, -1, 4)).toBe(0);
  });

  it("wraps at both ends so stepping is never a dead key", () => {
    expect(stepFrameIndex(3, 1, 4)).toBe(0);
    expect(stepFrameIndex(0, -1, 4)).toBe(3);
  });

  it("stays at 0 with no frames", () => {
    expect(stepFrameIndex(0, 1, 0)).toBe(0);
  });
});

describe("clampFrameIndex", () => {
  it("keeps a scrubbed value inside the frame list", () => {
    expect(clampFrameIndex(-3, 4)).toBe(0);
    expect(clampFrameIndex(9, 4)).toBe(3);
    expect(clampFrameIndex(2, 4)).toBe(2);
    expect(clampFrameIndex(2, 0)).toBe(0);
  });
});

describe("timelineFeatures", () => {
  it("carries the current frame plus two ghost frames, tagged by age", () => {
    const fc = timelineFeatures(FRAMES, 2);
    const ages = fc.features.map((f) => f.properties!.age);
    expect(new Set(ages)).toEqual(new Set([0, 1, 2]));
    expect(fc.features.filter((f) => f.properties!.age === 0)).toHaveLength(1);
    expect(fc.features.filter((f) => f.properties!.age === 1)).toHaveLength(2);
    expect(fc.features.filter((f) => f.properties!.age === 2)).toHaveLength(1);
  });

  it("does not reach back past the start of the day", () => {
    const fc = timelineFeatures(FRAMES, 0);
    expect(fc.features.every((f) => f.properties!.age === 0)).toBe(true);
    expect(fc.features).toHaveLength(1);
  });

  it("renders the current frame last so it paints over its own ghosts", () => {
    const ages = timelineFeatures(FRAMES, 2).features.map((f) => f.properties!.age);
    expect(ages).toEqual([...ages].sort((a, b) => (b as number) - (a as number)));
  });

  it("puts the delay and position of each point on the feature", () => {
    const fc = timelineFeatures(FRAMES, 0);
    const feature = fc.features[0];
    expect(feature.geometry.coordinates).toEqual([140, 40]);
    expect(feature.properties).toMatchObject({ stop_id: "A", delay_min: 1, age: 0 });
  });

  it("is empty for an out-of-range index or an empty day", () => {
    expect(timelineFeatures(FRAMES, 99).features).toHaveLength(0);
    expect(timelineFeatures([], 0).features).toHaveLength(0);
  });

  it("gives the two ghosts distinctly weaker opacities than the current frame", () => {
    expect(GHOST_OPACITY).toEqual([1, 0.25, 0.1]);
  });
});
