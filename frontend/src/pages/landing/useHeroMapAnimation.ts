import { useEffect, useEffectEvent, useRef, useState, type RefObject } from "react";
import { DELAY_RAMP } from "../../styles/tokens";
import type { HeroLabels, HeroPalette } from "./heroCanvas";
import { drawHeroFrame } from "./heroMapDraw";
import { isHexColor, luminance, mixHex } from "./heroMapMath";
import { DURATION, REPLAY_START, frameAt } from "./heroMapTimeline";

/** Map-only tints with no theme token (water, parks, roads, railways),
 *  chosen per theme so the basemap reads like the app's own tiles. */
const LIGHT_MAP = { water: "#D3E2E8", park: "#DDE9D8", road: "#FFFFFF", rail: "#7D838A" };
const DARK_MAP = { water: "#15283A", park: "#17291F", road: "#252B45", rail: "#6B7280" };

/** Canvas cannot read `var()`, so tokens are resolved once at mount. Values
 *  that fail to resolve to `#rrggbb` fall back to the light theme's. */
function resolvePalette(el: Element): HeroPalette {
  const style = getComputedStyle(el);
  const color = (name: string, fallback: string) => {
    const value = style.getPropertyValue(name).trim();
    return isHexColor(value) ? value : fallback;
  };
  const font = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
  const page = color("--bg-page", "#fafaf8");
  const dark = luminance(page) < 0.2;
  const map = dark ? DARK_MAP : LIGHT_MAP;
  return {
    land: dark ? mixHex(page, "#ffffff", 0.03) : mixHex(page, "#d8d4c8", 0.35),
    ...map,
    surface: color("--bg-surface", "#ffffff"),
    text: color("--text-primary", "#2a2a2a"),
    muted: color("--text-tertiary", "#6e6e6e"),
    rule: color("--border-subtle", "#e2e2e0"),
    accent: color("--accent", "#2750C2"),
    delay: {
      ok: DELAY_RAMP.ok,
      mild: DELAY_RAMP.mild,
      moderate: DELAY_RAMP.moderate,
      severe: color("--delay-severe", "#A8391F"),
    },
    warning: color("--color-warning-text", "#81631D"),
    fontBody: font("--font-body", "sans-serif"),
    fontMono: font("--font-mono", "monospace"),
  };
}

/** High-DPI canvases cost fill-rate quadratically; past 2x the extra
 *  sharpness is not visible on a moving scene. */
const MAX_PIXEL_RATIO = 2;

/** Largest frame-to-frame step the clock accepts, so a stalled or throttled
 *  tab resumes where it paused instead of skipping ahead through the script. */
const MAX_STEP_MS = 100;

/** Where the scene stands: playing, paused by the visitor, finished after
 *  its one pass, or a still frame that never moves (reduced motion). */
export type HeroPlayback = "playing" | "paused" | "ended" | "still";

type Controls = { pause: () => void; resume: () => void; replay: () => void };

function prefersReducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Plays the hero's sequence once on a caller-owned canvas and stops on its
 *  last frame, the settled live-operations view; `toggle` pauses and resumes
 *  it, and replays it from REPLAY_START once it has ended. The clock only
 *  advances while the canvas is on screen and the tab is visible, so a
 *  visitor who scrolls away and back does not miss it. Under reduced motion
 *  it draws that settled frame once, never schedules a frame and offers no
 *  playback. */
export function useHeroMapAnimation(
  canvasRef: RefObject<HTMLCanvasElement | null>,
  labels: HeroLabels,
): { playback: HeroPlayback; toggle: () => void } {
  const [playback, setPlayback] = useState<HeroPlayback>(() => (prefersReducedMotion() ? "still" : "playing"));
  // The running loop's controls, set by the effect that owns the loop.
  const controls = useRef<Controls | null>(null);

  // Reads the latest labels without restarting the loop when the language changes.
  const render = useEffectEvent((ctx: CanvasRenderingContext2D, width: number, height: number, t: number, palette: HeroPalette) => {
    drawHeroFrame(ctx, width, height, frameAt(t), palette, labels);
  });
  const ended = useEffectEvent(() => setPlayback("ended"));

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const maybeCtx = canvas.getContext("2d");
    // No 2D canvas (jsdom, or a browser without it): leave the canvas empty.
    if (!maybeCtx) return;
    const ctx: CanvasRenderingContext2D = maybeCtx;
    const el: HTMLCanvasElement = canvas;

    const palette = resolvePalette(el);
    const reducedMotion = prefersReducedMotion();

    let width = 0;
    let height = 0;
    let t = reducedMotion ? REPLAY_START : 0;
    // The first pass starts as soon as the effect has set up, so its first
    // frame is the first paint.
    let running = !reducedMotion;
    let rafId = 0;
    let last = 0;

    const draw = () => render(ctx, width, height, t, palette);

    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO);
      width = el.clientWidth;
      height = el.clientHeight;
      el.width = Math.max(1, Math.round(width * dpr));
      el.height = Math.max(1, Math.round(height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      // A running loop repaints on its next frame; a still one repaints now.
      if (!running) draw();
    }
    resize();
    window.addEventListener("resize", resize);
    if (reducedMotion) return () => window.removeEventListener("resize", resize);

    let onScreen = true;
    const observer =
      typeof IntersectionObserver === "undefined"
        ? null
        : new IntersectionObserver((entries) => {
            onScreen = entries.some((entry) => entry.isIntersecting);
          });
    observer?.observe(el);

    function frame(now: number) {
      const step = Math.min(now - last, MAX_STEP_MS);
      last = now;
      if (!document.hidden && onScreen) {
        t = Math.min(DURATION, t + step / 1000);
        draw();
      }
      if (t >= DURATION) {
        running = false;
        ended();
        return;
      }
      rafId = requestAnimationFrame(frame);
    }
    function start() {
      running = true;
      last = performance.now();
      rafId = requestAnimationFrame(frame);
    }
    function stop() {
      running = false;
      cancelAnimationFrame(rafId);
    }
    controls.current = {
      pause: stop,
      resume: start,
      replay: () => {
        t = REPLAY_START;
        draw();
        start();
      },
    };
    start();

    return () => {
      stop();
      controls.current = null;
      observer?.disconnect();
      window.removeEventListener("resize", resize);
    };
  }, [canvasRef]);

  function toggle() {
    if (playback === "playing") {
      controls.current?.pause();
      setPlayback("paused");
    } else if (playback === "paused") {
      controls.current?.resume();
      setPlayback("playing");
    } else if (playback === "ended") {
      controls.current?.replay();
      setPlayback("playing");
    }
  }

  return { playback, toggle };
}
