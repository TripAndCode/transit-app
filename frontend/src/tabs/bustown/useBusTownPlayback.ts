import { useEffect, useEffectEvent, useRef, useState, type RefObject } from "react";
import type { StopBounds } from "../../api/types";
import { drawBusTownFrame, resolveBusTownPalette } from "./busTownDraw";
import { fitProjection, positionsAt, type BusTownModel, type StopPair, type StopPoint } from "./busTownModel";

/** High-DPI canvases cost fill-rate quadratically; past 2x the extra
 *  sharpness is not visible on a moving scene. */
const MAX_PIXEL_RATIO = 2;
/** Largest frame-to-frame step the clock accepts, so a stalled or throttled
 *  tab resumes where it paused instead of jumping buses ahead through it. */
const MAX_STEP_MS = 100;

export type BusTownPlayback = {
  /** The playhead, throttled to once per simulated minute so the scrubber
   *  and clock re-render far less often than the canvas redraws. */
  sec: number;
  playing: boolean;
  play: () => void;
  pause: () => void;
  seek: (sec: number) => void;
};

type LoopControls = { seek: (sec: number) => void; setPlaying: (playing: boolean) => void };

/**
 * Owns the schematic's rAF draw loop and the playhead clock it advances.
 * `model`/`stopPoints`/`stopPairs`/`bounds` are read fresh on every drawn
 * frame without restarting the loop, so a newly loaded window never resets
 * play position. The loop is only ever scheduled while playing (and the tab
 * is visible and the canvas on screen) — pausing cancels it outright rather
 * than idling, matching the stricter of the two product requirements this
 * mirrors `useHeroMapAnimation`'s structure to meet.
 */
export function useBusTownPlayback(
  canvasRef: RefObject<HTMLCanvasElement | null>,
  model: BusTownModel,
  stopPoints: readonly StopPoint[],
  stopPairs: readonly StopPair[],
  bounds: StopBounds | null,
  minSec: number,
  maxSec: number,
  speed: number,
  autoplay: boolean,
): BusTownPlayback {
  const [sec, setSec] = useState(minSec);
  const [playing, setPlaying] = useState(autoplay);
  const speedRef = useRef(speed);
  useEffect(() => {
    speedRef.current = speed;
  }, [speed]);

  const controls = useRef<LoopControls | null>(null);

  const draw = useEffectEvent(
    (currentSec: number, ctx: CanvasRenderingContext2D, width: number, height: number, el: Element) => {
      const projection = bounds ? fitProjection(bounds, width, height) : null;
      drawBusTownFrame(ctx, width, height, projection, stopPoints, stopPairs, positionsAt(model, currentSec), resolveBusTownPalette(el));
    },
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const maybeCtx = canvas.getContext("2d");
    if (!maybeCtx) return;
    const ctx: CanvasRenderingContext2D = maybeCtx;
    const el: HTMLCanvasElement = canvas;

    let width = 0;
    let height = 0;
    let t = minSec;
    let lastMinute = Math.floor(t / 60);
    let running = false;
    let wantPlaying = false;
    let onScreen = true;
    let rafId = 0;
    let last = 0;

    function paint() {
      draw(t, ctx, width, height, el);
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
      t = Math.min(maxSec, t + (step / 1000) * speedRef.current);
      paint();
      const minute = Math.floor(t / 60);
      if (minute !== lastMinute) {
        lastMinute = minute;
        setSec(t);
      }
      if (t >= maxSec) {
        stop();
        setSec(t);
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
    };

    return () => {
      stop();
      controls.current = null;
      observer?.disconnect();
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("resize", resize);
    };
  }, [canvasRef, minSec, maxSec]);

  useEffect(() => {
    controls.current?.setPlaying(playing);
  }, [playing]);

  return {
    sec,
    playing,
    play: () => setPlaying(true),
    pause: () => setPlaying(false),
    seek: (next) => {
      const clamped = Math.max(minSec, Math.min(maxSec, next));
      controls.current?.seek(clamped);
      setSec(clamped);
    },
  };
}
