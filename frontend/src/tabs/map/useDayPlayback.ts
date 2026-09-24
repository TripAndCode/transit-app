import { useEffect, useEffectEvent, useRef, useState } from "react";
import { useTimeline } from "../../api/hooks";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import type { TimelineFrame } from "../../api/types";
import { FRAME_MS, clampFrameIndex, frameIndexAt, stepFrameIndex } from "./playbackFrames";

type PlaybackSpeed = 1 | 2;

export type PlaybackController = {
  frames: TimelineFrame[];
  date: string | null;
  index: number;
  playing: boolean;
  speed: PlaybackSpeed;
  /** True when the viewer asked for less motion: playback never auto-advances
   *  and the rail offers stepping instead. */
  steppingOnly: boolean;
  loading: boolean;
  error: unknown;
  setIndex: (index: number) => void;
  step: (delta: number) => void;
  toggle: () => void;
  pause: () => void;
  cycleSpeed: () => void;
};

/** How often the clock re-reads the playhead. Well inside one frame's dwell,
 *  so a frame boundary is never noticeably late, and cheap enough that it can
 *  run off a plain interval instead of a render loop. */
const TICK_MS = 100;

/** One shared empty list, so a day that has not arrived yet does not hand the
 *  map layer a new array identity on every render and make it re-run. */
const NO_FRAMES: TimelineFrame[] = [];

/**
 * Owns one day's frames and the playhead over them.
 *
 * The playhead is derived from elapsed wall-clock time through the pure
 * `frameIndexAt`, not incremented per tick: a tick the browser delayed (a
 * background tab, a long paint) then lands on the frame the clock says it is,
 * rather than leaving playback permanently behind by however much it drifted.
 */
export function useDayPlayback(agencyId: number | null, active: boolean): PlaybackController {
  const steppingOnly = useMediaQuery("(prefers-reduced-motion: reduce)");
  const query = useTimeline(agencyId, active);
  const frames = query.data?.frames ?? NO_FRAMES;
  const frameCount = frames.length;

  const [index, setIndexState] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<PlaybackSpeed>(1);
  // Wall-clock origin the playhead is measured from. A ref, not state: it is
  // an input to the clock, never something a render reads.
  const originRef = useRef(0);

  const tick = useEffectEvent(() => {
    const next = frameIndexAt(performance.now() - originRef.current, frameCount, speed);
    setIndexState((current) => (current === next ? current : next));
  });

  useEffect(() => {
    if (!playing || frameCount === 0) return;
    const timer = window.setInterval(() => tick(), TICK_MS);
    return () => window.clearInterval(timer);
  }, [frameCount, playing]);

  // A day that loads shorter than the playhead (a 60-minute step replacing a
  // 15-minute one, or an agency switch) must not leave the rail pointing past
  // its own end. Derived during render rather than synced in an effect.
  const safeIndex = clampFrameIndex(index, frameCount);

  function seek(next: number) {
    // Re-anchor the clock so playback resumes from where it was put, instead
    // of snapping back to wherever the original origin had reached.
    originRef.current = performance.now() - (next * FRAME_MS) / speed;
    setIndexState(next);
  }

  return {
    frames,
    date: query.data?.date ?? null,
    index: safeIndex,
    playing,
    speed,
    steppingOnly,
    loading: query.isLoading,
    error: query.error,
    setIndex: (next) => seek(clampFrameIndex(next, frameCount)),
    step: (delta) => {
      setPlaying(false);
      seek(stepFrameIndex(safeIndex, delta, frameCount));
    },
    toggle: () => {
      if (steppingOnly || frameCount === 0) return;
      if (!playing) originRef.current = performance.now() - (safeIndex * FRAME_MS) / speed;
      setPlaying(!playing);
    },
    pause: () => setPlaying(false),
    cycleSpeed: () => {
      const next: PlaybackSpeed = speed === 1 ? 2 : 1;
      originRef.current = performance.now() - (safeIndex * FRAME_MS) / next;
      setSpeed(next);
    },
  };
}
