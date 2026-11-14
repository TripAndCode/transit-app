import { useEffect, useEffectEvent, useRef, useState } from "react";
import type { StopBounds } from "../../api/types";
import { useThemeSignal } from "../../styles/theme";
import { drawBusTownFrame, resolveBusTownPalette, type BusTownPalette } from "./busTownDraw";
import { fitProjection, positionsAt, type BusTownModel, type StopPair, type StopPoint } from "./busTownModel";

/** High-DPI canvases cost fill-rate quadratically; past 2x the extra
 *  sharpness is not visible on a moving scene. */
const MAX_PIXEL_RATIO = 2;
/** Largest frame-to-frame step the clock accepts, so a stalled or throttled
 *  tab resumes where it paused instead of jumping buses ahead through it. */
const MAX_STEP_MS = 100;

export type BusTownPlayback = {
  playing: boolean;
  play: () => void;
  pause: () => void;
  seek: (sec: number) => void;
  /** Attach to the `<canvas>` as `ref`. A callback, not a `RefObject`, so the
   *  setup effect below re-runs the moment the element actually mounts —
   *  which on a first, uncached visit happens after this hook's caller has
   *  already rendered once, behind `AsyncSection`'s loading skeleton. */
  canvasRef: (el: HTMLCanvasElement | null) => void;
};

export type BusTownPlaybackOptions = {
  model: BusTownModel;
  stopPoints: readonly StopPoint[];
  stopPairs: readonly StopPair[];
  bounds: StopBounds | null;
  minSec: number;
  maxSec: number;
  speed: number;
  autoplay: boolean;
  /** The playhead, owned by the caller (throttled to once per simulated
   *  minute by this hook's own calls to `setSec`) so it can feed both this
   *  hook and whatever else needs to know where playback is without the two
   *  racing to own it. */
  sec: number;
  setSec: (sec: number) => void;
};

type LoopControls = {
  seek: (sec: number) => void;
  setPlaying: (playing: boolean) => void;
  retheme: () => void;
  repaint: () => void;
};

/**
 * Owns the schematic's rAF draw loop. `model`/`stopPoints`/`stopPairs`/
 * `bounds` are read fresh on every drawn frame without restarting the loop,
 * so a newly loaded window never resets play position. A separate effect
 * also repaints the current frame whenever any of them changes, so while
 * paused a day switch, a window arriving after a paused seek, or
 * the stop bounds resolving are never left stale on screen. The loop itself
 * is only ever scheduled while playing (and the tab is visible and the
 * canvas on screen) — pausing cancels it outright rather than scheduling
 * empty frames.
 */
export function useBusTownPlayback({
  model,
  stopPoints,
  stopPairs,
  bounds,
  minSec,
  maxSec,
  speed,
  autoplay,
  sec,
  setSec,
}: BusTownPlaybackOptions): BusTownPlayback {
  /** The canvas itself lives in a ref, not state: the setup effect below
   *  resizes it directly (`el.width`/`el.height`), which the compiler only
   *  allows on a ref's `current` — a value held in state is treated as
   *  immutable. `canvasMounted` is the reactive signal that tells the effect
   *  the ref now has something to attach to. */
  const canvasElRef = useRef<HTMLCanvasElement | null>(null);
  const [canvasMounted, setCanvasMounted] = useState(false);
  const [playing, setPlaying] = useState(autoplay);
  const currentSpeed = useEffectEvent(() => speed);
  const themeSignal = useThemeSignal();

  const controls = useRef<LoopControls | null>(null);

  const draw = useEffectEvent(
    (currentSec: number, ctx: CanvasRenderingContext2D, width: number, height: number, palette: BusTownPalette) => {
      const projection = bounds ? fitProjection(bounds, width, height) : null;
      drawBusTownFrame(ctx, width, height, projection, stopPoints, stopPairs, positionsAt(model, currentSec), palette);
    },
  );

  /** The playhead/play state live at the instant the setup effect below
   *  (re-)runs — read through `useEffectEvent` so referencing them doesn't
   *  force the effect to restart on every tick, the way putting `sec`/
   *  `playing` directly in its dependency array would. */
  const live = useEffectEvent(() => ({ sec, playing }));

  useEffect(() => {
    if (!canvasElRef.current) return;
    const maybeCtx = canvasElRef.current.getContext("2d");
    if (!maybeCtx) return;
    const ctx: CanvasRenderingContext2D = maybeCtx;
    const el: HTMLCanvasElement = canvasElRef.current;

    let palette = resolveBusTownPalette(el);
    let width = 0;
    let height = 0;
    const { sec: liveSec, playing: livePlaying } = live();
    let t = Math.max(minSec, Math.min(maxSec, liveSec));
    let lastMinute = Math.floor(t / 60);
    let running = false;
    let wantPlaying = livePlaying;
    let onScreen = true;
    let rafId = 0;
    let last = 0;

    function paint() {
      draw(t, ctx, width, height, palette);
    }

    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO);
      width = el.clientWidth;
      height = el.clientHeight;
      el.width = Math.max(1, Math.round(width * dpr));
      el.height = Math.max(1, Math.round(height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      paint();
    }
    resize();
    window.addEventListener("resize", resize);

    function frame(now: number) {
      const step = Math.min(now - last, MAX_STEP_MS);
      last = now;
      t = Math.min(maxSec, t + (step / 1000) * currentSpeed());
      paint();
      const minute = Math.floor(t / 60);
      if (minute !== lastMinute) {
        lastMinute = minute;
        setSec(t);
      }
      if (t >= maxSec) {
        wantPlaying = false;
        stop();
        setSec(t);
        setPlaying(false);
        return;
      }
      rafId = requestAnimationFrame(frame);
    }
    function start() {
      if (running) return;
      running = true;
      last = performance.now();
      rafId = requestAnimationFrame(frame);
    }
    function stop() {
      running = false;
      cancelAnimationFrame(rafId);
    }
    function sync() {
      if (wantPlaying && onScreen && !document.hidden) start();
      else stop();
    }

    const observer =
      typeof IntersectionObserver === "undefined"
        ? null
        : new IntersectionObserver((entries) => {
            onScreen = entries.some((entry) => entry.isIntersecting);
            sync();
          });
    observer?.observe(el);

    const onVisibilityChange = () => sync();
    document.addEventListener("visibilitychange", onVisibilityChange);

    controls.current = {
      seek: (next) => {
        t = Math.max(minSec, Math.min(maxSec, next));
        lastMinute = Math.floor(t / 60);
        paint();
        setSec(t);
      },
      setPlaying: (next) => {
        wantPlaying = next;
        sync();
      },
      retheme: () => {
        palette = resolveBusTownPalette(el);
        paint();
      },
      repaint: () => {
        paint();
      },
    };
    sync();

    return () => {
      stop();
      controls.current = null;
      observer?.disconnect();
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("resize", resize);
    };
  }, [canvasMounted, minSec, maxSec, setSec]);

  useEffect(() => {
    controls.current?.setPlaying(playing);
  }, [playing]);

  useEffect(() => {
    controls.current?.retheme();
  }, [themeSignal]);

  useEffect(() => {
    controls.current?.repaint();
  }, [model, stopPoints, stopPairs, bounds]);

  function seek(next: number) {
    const clamped = Math.max(minSec, Math.min(maxSec, next));
    controls.current?.seek(clamped);
    setSec(clamped);
  }

  return {
    playing,
    play: () => {
      if (sec >= maxSec) seek(minSec);
      setPlaying(true);
    },
    pause: () => setPlaying(false),
    seek,
    canvasRef: (el: HTMLCanvasElement | null) => {
      canvasElRef.current = el;
      setCanvasMounted(el != null);
    },
  };
}
