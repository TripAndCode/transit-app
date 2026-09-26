// Shared contract and small drawing primitives for the landing hero's
// canvas: the resolved palette, the pre-translated labels, and helpers for
// the app-style cards, pills and haloed map text both renderers use.

import { delayBand } from "../../styles/tokens";
import { withAlpha } from "./heroMapMath";
import type { RouteKey, StopKey } from "./heroMapScene";
import type { CaptionIndex } from "./heroMapTimeline";

export type Ctx = CanvasRenderingContext2D;

/** Theme tokens resolved to `#rrggbb` (canvas cannot read `var()`), the
 *  app's own delay ramp, and map-only tints picked for the active theme. */
export type HeroPalette = {
  land: string;
  water: string;
  park: string;
  road: string;
  rail: string;
  surface: string;
  text: string;
  muted: string;
  rule: string;
  accent: string;
  delay: { ok: string; mild: string; moderate: string; severe: string };
  fontBody: string;
  fontMono: string;
};

export type HeroLabels = {
  screens: { live: string; period: string };
  captions: Record<CaptionIndex, string>;
  routes: Record<RouteKey, string>;
  stops: Record<StopKey, string>;
  queueTitle: string;
  kpi: { observed: string; delayedFivePlus: string; onTime: string };
  legendBands: readonly [string, string, string, string];
  legendDisclosure: string;
  tripHeading: string;
  tripChartTitle: string;
  tripNote: string;
  refresh: string;
  playback: string;
  playbackSpeed: string;
  hourlyTitle: string;
  peak: string;
  overviewAverage: string;
  overviewChange: string;
  overviewDelayedRoutes: string;
  routesToCheck: string;
  sampleNotice: string;
  /** "+6分" — whole or one-decimal minutes, already formatted by the caller. */
  delayShort: (minutes: number) => string;
  /** "6.6分" */
  minutes: (minutes: number) => string;
  hourOfDay: (hour: number) => string;
  clock: (hour: number) => string;
  /** Unit suffix beside a large number ("分"). */
  minuteUnit: string;
};

/** The ramp color the app itself uses for `minutes` of delay — same
 *  thresholds (`delayBand`) as every other delay mark in the product. */
export function delayColorIn(palette: HeroPalette, minutes: number): string {
  return palette.delay[delayBand(minutes)];
}

export function setFont(ctx: Ctx, weight: number, px: number, family: string): void {
  ctx.font = `${weight} ${px}px ${family}`;
}

export function roundRect(ctx: Ctx, x: number, y: number, w: number, h: number, r: number): void {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
}

/** A panel surface in the app's card style: soft drop shadow, hairline
 *  border. The shadow is two offset low-alpha fills rather than
 *  `shadowBlur`, because cards are repainted every frame and a blur pass
 *  per card per frame is far more expensive than two flat fills. */
export function drawCard(ctx: Ctx, palette: HeroPalette, x: number, y: number, w: number, h: number, radius: number, alpha = 1): void {
  if (alpha <= 0) return;
  ctx.save();
  ctx.globalAlpha *= alpha;
  ctx.fillStyle = "rgba(0,0,0,0.05)";
  roundRect(ctx, x - 1, y + 3, w + 2, h + 4, radius + 1);
  ctx.fill();
  ctx.fillStyle = "rgba(0,0,0,0.04)";
  roundRect(ctx, x, y + 1.5, w, h + 1.5, radius);
  ctx.fill();
  ctx.fillStyle = palette.surface;
  roundRect(ctx, x, y, w, h, radius);
  ctx.fill();
  ctx.strokeStyle = palette.rule;
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.restore();
}

/** Small tinted pill naming which screen of the app a panel section is from. */
export function drawScreenTag(ctx: Ctx, palette: HeroPalette, x: number, y: number, label: string, px: number): void {
  setFont(ctx, 700, px, palette.fontBody);
  const w = ctx.measureText(label).width + px * 1.8;
  const h = px * 1.9;
  roundRect(ctx, x, y - h / 2, w, h, h / 2);
  ctx.fillStyle = withAlpha(palette.accent, 0.12);
  ctx.fill();
  ctx.fillStyle = palette.accent;
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText(label, x + px * 0.9, y);
}

/** Map label with a surface-colored halo so it reads over any basemap. */
export function haloText(ctx: Ctx, palette: HeroPalette, text: string, x: number, y: number): void {
  ctx.lineWidth = 3;
  ctx.strokeStyle = palette.surface;
  ctx.strokeText(text, x, y);
  ctx.fillStyle = palette.text;
  ctx.fillText(text, x, y);
}
