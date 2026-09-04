import { useEffect, useRef, type RefObject } from "react";
import { buildCityScene, buildVehicles, advanceVehicle, poseAtT, type Vehicle } from "./cityMapScene";
import { drawScene, type VehicleDraw } from "./cityMapDraw";

const FALLBACK_ON_TIME_COLOR = "#1A8A72";
const FALLBACK_DELAYED_COLOR = "#C99A2E";

/** Reads the app's real semantic route-color tokens from the DOM
 *  (`--accent` = on-time, `--color-warning` = elevated delay) rather than
 *  hardcoding hex values, so every consumer's two route colors always match
 *  whatever the active theme resolves those meanings to. Canvas drawing
 *  can't reference a CSS var() directly, so this resolves it once, in JS,
 *  at mount. Falls back to the dark theme's own values if the tokens are
 *  somehow unset (e.g. `global.css` failed to load). */
function resolveRouteColors(): { onTime: string; delayed: string } {
  if (typeof window === "undefined") {
    return { onTime: FALLBACK_ON_TIME_COLOR, delayed: FALLBACK_DELAYED_COLOR };
  }
  const style = getComputedStyle(document.documentElement);
  const onTime = style.getPropertyValue("--accent").trim();
  const delayed = style.getPropertyValue("--color-warning").trim();
  return {
    onTime: onTime || FALLBACK_ON_TIME_COLOR,
    delayed: delayed || FALLBACK_DELAYED_COLOR,
  };
}

/** Runs the shared schematic-city rAF animation loop (procedurally-drawn
 *  blocks/park/river, two metro-style routes, and looping vehicle markers)
 *  against a caller-owned canvas. Extracted so both the hero's tilted scene
 *  (`CityMapHero`) and the dashboard-preview Map panel's untilted,
 *  floating-controls version (`PreviewMapCanvas`) share one implementation
 *  instead of two copies of the same rAF/resize bookkeeping.
 *
 *  The render loop lives entirely inside this effect's own
 *  `requestAnimationFrame`, driven by a ref (`vehiclesRef`), never React
 *  state -- advancing 4 vehicles 60 times a second by calling setState would
 *  re-render the caller (and re-run every hook below it) every frame for a
 *  value nothing else on the page reads. Purely decorative -- callers mark
 *  their `<canvas>` `aria-hidden`. */
export function useCityMapAnimation(canvasRef: RefObject<HTMLCanvasElement | null>): void {
  const vehiclesRef = useRef<Vehicle[]>([]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const maybeCtx = canvas.getContext("2d");
    // No 2D canvas support (e.g. jsdom in tests, or a browser build without
    // it) -- degrade to an empty decorative canvas instead of throwing.
    if (!maybeCtx) return;
    // Rebound to a definitely-non-null const: TypeScript's control-flow
    // narrowing from the guard above doesn't extend into the nested
    // `resize`/`frame` function declarations below (they could in principle
    // run after further reassignment), so referencing `maybeCtx` directly
    // inside them would still type as possibly-null.
    const ctx: CanvasRenderingContext2D = maybeCtx;

    const colors = resolveRouteColors();
    const scene = buildCityScene();
    const routesById = new Map(scene.routes.map((route) => [route.id, route]));
    vehiclesRef.current = buildVehicles(scene.routes);

    let width = 0;
    let height = 0;

    function resize() {
      if (!canvas) return;
      const dpr = window.devicePixelRatio || 1;
      // clientWidth/clientHeight reflect the element's own layout box, not
      // its CSS-transformed (tilted) on-screen bounding rect -- using
      // getBoundingClientRect() here would size the canvas's internal
      // pixel grid to the post-perspective rect instead of its actual
      // layout dimensions.
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();
    window.addEventListener("resize", resize);

    let rafId = 0;
    let lastFrameTime = performance.now();
    function frame(now: number) {
      rafId = requestAnimationFrame(frame);
      const dtMs = Math.min(now - lastFrameTime, 100);
      lastFrameTime = now;
      // Skip work (but keep scheduling frames so the loop resumes cleanly)
      // while the tab is backgrounded.
      if (document.hidden) return;

      vehiclesRef.current = vehiclesRef.current.map((v) => advanceVehicle(v, dtMs));
      const vehicleDraws: VehicleDraw[] = vehiclesRef.current.map((v) => {
        const route = routesById.get(v.routeId)!;
        return { pose: poseAtT(route, v.t), colorVar: route.colorVar, mode: route.vehicleMode };
      });
      drawScene(ctx, width, height, scene, vehicleDraws, colors);
    }
    rafId = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener("resize", resize);
    };
  }, [canvasRef]);
}
