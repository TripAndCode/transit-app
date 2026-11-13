import { isHexColor } from "../../pages/landing/heroMapMath";
import { delayColor, DELAY_THRESHOLDS } from "../../styles/tokens";
import type { BusTownBus, Projection, StopPair, StopPoint } from "./busTownModel";

export type BusTownPalette = {
  background: string;
  line: string;
  stopDot: string;
  stopRing: string;
  /** The severe-delay band's resolved hex, read once per palette (mount and
   *  theme toggle) rather than per bus per frame — `getComputedStyle` is a
   *  layout-triggering call the draw loop can't afford on every bus. */
  severe: string;
};

/** Canvas cannot read `var()`, so the schematic's own tokens are resolved
 *  once at mount and again whenever the theme changes. */
export function resolveBusTownPalette(el: Element): BusTownPalette {
  const style = getComputedStyle(el);
  const color = (name: string, fallback: string) => {
    const value = style.getPropertyValue(name).trim();
    return isHexColor(value) ? value : fallback;
  };
  return {
    background: color("--bg-surface", "#ffffff"),
    line: color("--border-subtle", "#D6DDE6"),
    stopDot: color("--text-tertiary", "#5E6982"),
    stopRing: color("--bg-page", "#F6F7FB"),
    severe: color("--delay-severe", "#A8391F"),
  };
}

const STOP_RADIUS = 2.5;
const BUS_RADIUS = 5;

/** Draws one frame of the schematic: faint lines between stops a trip visits
 *  consecutively, a small dot per stop, and a larger dot per bus coloured by
 *  its current delay band. Stops and buses with no projectable coordinate
 *  (an unresolved `projection`, before the agency's own stop bounds load)
 *  are simply not drawn yet. */
export function drawBusTownFrame(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  projection: Projection | null,
  stopPoints: readonly StopPoint[],
  stopPairs: readonly StopPair[],
  buses: readonly BusTownBus[],
  palette: BusTownPalette,
): void {
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = palette.background;
  ctx.fillRect(0, 0, width, height);
  if (!projection) return;

  if (stopPairs.length > 0) {
    ctx.strokeStyle = palette.line;
    ctx.lineWidth = 1;
    ctx.globalAlpha = 0.5;
    ctx.beginPath();
    for (const pair of stopPairs) {
      const a = projection(pair.lat0, pair.lon0);
      const b = projection(pair.lat1, pair.lon1);
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  if (stopPoints.length > 0) {
    ctx.fillStyle = palette.stopDot;
    ctx.beginPath();
    for (const stop of stopPoints) {
      const p = projection(stop.lat, stop.lon);
      ctx.moveTo(p.x + STOP_RADIUS, p.y);
      ctx.arc(p.x, p.y, STOP_RADIUS, 0, Math.PI * 2);
    }
    ctx.fill();
  }

  for (const bus of buses) {
    const p = projection(bus.lat, bus.lon);
    ctx.fillStyle = bus.delayMin < DELAY_THRESHOLDS.severe ? delayColor(bus.delayMin) : palette.severe;
    ctx.strokeStyle = palette.stopRing;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(p.x, p.y, BUS_RADIUS, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
}
