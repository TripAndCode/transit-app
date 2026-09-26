import { useEffect, useEffectEvent, type RefObject } from "react";
import { drawHeroFrame, type HeroLabels, type HeroPalette } from "./heroMapDraw";
import { isHexColor } from "./heroMapMath";
import { buildHeroMap, type HeroMap } from "./heroMapScene";
import { SEQUENCE_END, frameAt } from "./heroMapTimeline";

/** Dark-theme values, used only if a token fails to resolve to `#rrggbb`
 *  (e.g. `global.css` did not load); the hero is always rendered dark. */
const FALLBACK_PALETTE: HeroPalette = {
  background: "#0F1119",
  text: "#FAFAFF",
  textMuted: "#C9CBDA",
  accent: "#43c5ba",
  accentStrong: "#6FD8CC",
  warning: "#C99A2E",
  fontBody: "sans-serif",
  fontMono: "monospace",
};

/** Canvas cannot read `var()`, so tokens are resolved once at mount — from
 *  the canvas itself rather than the document root, because the hero scopes
 *  the dark theme to its own section. */
function resolvePalette(el: Element): HeroPalette {
  const style = getComputedStyle(el);
  const color = (name: string, fallback: string) => {
    const value = style.getPropertyValue(name).trim();
    return isHexColor(value) ? value : fallback;
  };
  const font = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
  return {
    background: color("--bg-page", FALLBACK_PALETTE.background),
    text: color("--text-primary", FALLBACK_PALETTE.text),
    textMuted: color("--text-tertiary", FALLBACK_PALETTE.textMuted),
    accent: color("--accent", FALLBACK_PALETTE.accent),
    accentStrong: color("--accent-strong", FALLBACK_PALETTE.accentStrong),
    warning: color("--color-warning", FALLBACK_PALETTE.warning),
    fontBody: font("--font-body", FALLBACK_PALETTE.fontBody),
    fontMono: font("--font-mono", FALLBACK_PALETTE.fontMono),
  };
}

/** High-DPI canvases cost fill-rate quadratically; past 2x the extra
 *  sharpness is not visible on a moving scene. */
const MAX_PIXEL_RATIO = 2;

/** Largest frame-to-frame step the clock accepts, so a stalled or throttled
 *  tab resumes where it paused instead of skipping ahead through the script. */
const MAX_STEP_MS = 100;

/** Plays the hero's live-map sequence on a caller-owned canvas. The script
 *  clock only advances while the canvas is on screen and the tab is visible,
 *  so a visitor who scrolls away and back does not miss it. Under reduced
 *  motion it draws the final state once and never schedules a frame. */
export function useHeroMapAnimation(canvasRef: RefObject<HTMLCanvasElement | null>, labels: HeroLabels): void {
  // Reads the latest labels without restarting the loop when the language
  // changes mid-sequence.
  const render = useEffectEvent(
    (ctx: CanvasRenderingContext2D, width: number, height: number, t: number, palette: HeroPalette, map: HeroMap) => {
      drawHeroFrame(ctx, width, height, frameAt(t), map, palette, labels);
    },
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const maybeCtx = canvas.getContext("2d");
    // No 2D canvas (jsdom, or a browser without it): leave the canvas empty.
    if (!maybeCtx) return;
    const ctx: CanvasRenderingContext2D = maybeCtx;
    const el: HTMLCanvasElement = canvas;

    const palette = resolvePalette(el);
    const map = buildHeroMap();
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let width = 0;
    let height = 0;
    let elapsedMs = 0;

    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO);
      width = el.clientWidth;
      height = el.clientHeight;
      el.width = Math.max(1, Math.round(width * dpr));
      el.height = Math.max(1, Math.round(height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (reducedMotion) render(ctx, width, height, SEQUENCE_END, palette, map);
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

    let rafId = 0;
    let last = performance.now();
    function frame(now: number) {
      rafId = requestAnimationFrame(frame);
      const step = Math.min(now - last, MAX_STEP_MS);
      last = now;
      if (document.hidden || !onScreen) return;
      elapsedMs += step;
      render(ctx, width, height, elapsedMs / 1000, palette, map);
    }
    rafId = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(rafId);
      observer?.disconnect();
      window.removeEventListener("resize", resize);
    };
  }, [canvasRef]);
}
