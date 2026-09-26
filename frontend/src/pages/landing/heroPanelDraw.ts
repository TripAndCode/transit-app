// The hero's right-hand panel, drawn in the app's own layout: the live
// queue (KPI tiles + trips to check), the opened trip's per-stop chart, the
// period overview's hourly bars, and its headline numbers. Also owns the
// geometry both morphs fly between (the trip chart box; the playback rail
// and the hourly bars), so source and target can never drift apart.

import {
  delayColorIn,
  drawCard,
  drawScreenTag,
  roundRect,
  setFont,
  type Ctx,
  type HeroLabels,
  type HeroPalette,
} from "./heroCanvas";
import { formatNumber } from "../../utils/format";
import { clamp01, expoInOut, expoOut, lerp, segment, withAlpha } from "./heroMapMath";
import {
  FIRST_SERVICE_HOUR,
  HOURLY_24,
  HOURLY_MEANS,
  KPI,
  OVERVIEW,
  PEAK_HOUR,
  QUEUE_ROWS,
  SELECTED_TRIP,
  TRIP_HISTORY,
} from "./heroMapScene";
import type { HeroFrame, PanelSection } from "./heroMapTimeline";

/** Below this width the headline spans the canvas: no panel, no morphs,
 *  and the map drops below the headline. Mirrors LandingPage.css. */
export const WIDE_LAYOUT_MIN_WIDTH = 900;
const PANEL_BASE_W = 374;
const PANEL_BASE_H = 440;
const BEAT = 60 / 128;
const RAIL_H = 46;

export type PanelGeom = { x: number; y: number; w: number; h: number; s: number; ix: number; iw: number };
export type HeroLayout = {
  wide: boolean;
  focusX: number;
  focusY: number;
  panel: PanelGeom | null;
  rail: { x: number; y: number; w: number };
  caption: { x: number; y: number };
};

/** The panel keeps its designed proportions and scales as one piece; the
 *  map's focal point sits in the free band between the headline column
 *  (left ~48%) and the panel. */
export function layoutFor(width: number, height: number): HeroLayout {
  if (width < WIDE_LAYOUT_MIN_WIDTH) {
    return {
      wide: false,
      focusX: width * 0.5,
      focusY: height * 0.7,
      panel: null,
      rail: { x: width * 0.06, y: height - RAIL_H - 20, w: width * 0.88 },
      caption: { x: width * 0.06, y: height * 0.6 },
    };
  }
  const s = Math.min(Math.min(400, Math.max(300, width * 0.26)) / PANEL_BASE_W, (height * 0.8) / PANEL_BASE_H);
  const w = PANEL_BASE_W * s;
  const h = PANEL_BASE_H * s;
  const x = width - w - Math.max(24, width * 0.03);
  const y = (height - h) / 2;
  const freeLeft = width * 0.48 + 16;
  const freeRight = x - 16;
  return {
    wide: true,
    focusX: (freeLeft + freeRight) / 2,
    focusY: height / 2,
    panel: { x, y, w, h, s, ix: x + 30 * s, iw: w - 60 * s },
    rail: { x: freeLeft, y: height - RAIL_H - 24, w: freeRight - freeLeft },
    caption: { x: freeLeft, y: height * 0.16 },
  };
}

export function railGeometry(rail: HeroLayout["rail"]) {
  const pad = RAIL_H * 0.3;
  const bx = rail.x + pad + RAIL_H * 0.55;
  const bw = rail.w - pad * 2 - RAIL_H * 1.6;
  return { ...rail, h: RAIL_H, pad, bx, bw, segW: bw / HOURLY_MEANS.length };
}
export type RailGeometry = ReturnType<typeof railGeometry>;

export function tripChartBox(p: PanelGeom) {
  return { x: p.ix, y: p.y + 190 * p.s, w: p.iw, h: 150 * p.s };
}
export type ChartBox = ReturnType<typeof tripChartBox>;

export function hourBarsBox(p: PanelGeom) {
  return { x0: p.ix, x1: p.ix + p.iw, base: p.y + 330 * p.s, maxH: 200 * p.s };
}
type BarsBox = ReturnType<typeof hourBarsBox>;

/** Chart position of the trip's i-th reported stop — the trail morph's target. */
export function tripChartPoint(box: ChartBox, i: number): [number, number] {
  const n = TRIP_HISTORY.length;
  return [box.x + (box.w * i) / (n - 1), box.y + box.h - (TRIP_HISTORY[i].delay / 7) * box.h];
}

function drawTripChart(ctx: Ctx, palette: HeroPalette, labels: HeroLabels, box: ChartBox, s: number): void {
  ctx.strokeStyle = palette.accent;
  ctx.lineWidth = 3;
  ctx.lineCap = "round";
  ctx.beginPath();
  TRIP_HISTORY.forEach((_, i) => {
    const [x, y] = tripChartPoint(box, i);
    if (i) ctx.lineTo(x, y);
    else ctx.moveTo(x, y);
  });
  ctx.stroke();
  TRIP_HISTORY.forEach((h, i) => {
    const [x, y] = tripChartPoint(box, i);
    ctx.fillStyle = delayColorIn(palette, h.delay);
    ctx.strokeStyle = palette.surface;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 5 * s, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    setFont(ctx, 700, 10 * s, palette.fontBody);
    ctx.fillStyle = palette.muted;
    ctx.textAlign = i === 0 ? "left" : i === TRIP_HISTORY.length - 1 ? "right" : "center";
    ctx.textBaseline = "top";
    ctx.fillText(labels.stops[h.stop], x, box.y + box.h + 10 * s);
  });
}

/** Hourly bars. While `morph` < 1 they are still flying up out of copies of
 *  the playback rail's segments (same hours, same means); once landed they
 *  light the hour the playback clock is on. */
export function drawHourBars(ctx: Ctx, palette: HeroPalette, box: BarsBox, morph: number, hour: number, rail: RailGeometry, s: number): void {
  const bw = (box.x1 - box.x0) / 24;
  const vmax = Math.max(...HOURLY_24);
  const current = Math.floor(hour);
  HOURLY_24.forEach((v, hr) => {
    const k = expoInOut(clamp01(morph * 1.4 - hr * 0.02));
    if (k <= 0) return;
    const hh = (v / vmax) * box.maxH;
    const inService = hr >= FIRST_SERVICE_HOUR;
    const src = inService
      ? { x: rail.bx + (hr - FIRST_SERVICE_HOUR) * rail.segW + 1, y: rail.y + rail.h * 0.42, w: rail.segW - 2, h: rail.h * 0.16 }
      : { x: box.x0 + hr * bw + 1.5, y: box.base, w: bw - 3, h: 0 };
    const x = lerp(src.x, box.x0 + hr * bw + 1.5, k);
    const y = lerp(src.y, box.base - hh, k);
    const w = lerp(src.w, bw - 3, k);
    const h = lerp(src.h, hh, k);
    if (h <= 0.5) return;
    let fill: string;
    if (k < 0.7 || hr === current) fill = delayColorIn(palette, v);
    else if (hr === PEAK_HOUR) fill = palette.delay.moderate;
    else fill = withAlpha(palette.accent, hr < current && inService ? 0.75 : 0.28);
    ctx.fillStyle = fill;
    roundRect(ctx, x, y, w, h, 2);
    ctx.fill();
  });
  if (morph >= 1) {
    setFont(ctx, 600, 10 * s, palette.fontMono);
    ctx.fillStyle = palette.muted;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (const hr of [0, 6, 12, 18]) ctx.fillText(String(hr), box.x0 + hr * bw + bw / 2, box.base + 6 * s);
  }
}

function drawKpiTiles(ctx: Ctx, palette: HeroPalette, labels: HeroLabels, p: PanelGeom, since: number): void {
  const s = p.s;
  const gap = 8 * s;
  const tw = (p.iw - gap * 2) / 3;
  const th = 70 * s;
  const items: [string, number, string, boolean][] = [
    [labels.kpi.observed, KPI.observed, "", false],
    [labels.kpi.delayedFivePlus, KPI.delayedFivePlus, "", true],
    [labels.kpi.onTime, KPI.onTimePct, "%", false],
  ];
  items.forEach(([label, value, suffix, flagged], i) => {
    const k = expoOut(segment(since, i * (BEAT / 2), 0.45 + i * (BEAT / 2)));
    if (k <= 0) return;
    const x = p.ix + i * (tw + gap);
    const y = p.y + 96 * s + (1 - k) * 8 * s;
    drawCard(ctx, palette, x, y, tw, th, 10 * s, k);
    ctx.save();
    ctx.globalAlpha *= k;
    setFont(ctx, 700, 10 * s, palette.fontBody);
    ctx.fillStyle = palette.muted;
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    ctx.fillText(label, x + tw * 0.1, y + 22 * s);
    const n = Math.round(value * expoOut(segment(since, 0.2 + i * (BEAT / 2), 0.9 + i * (BEAT / 2))));
    setFont(ctx, 800, Math.min(26 * s, tw * 0.24), palette.fontBody);
    ctx.fillStyle = flagged ? palette.delay.severe : palette.text;
    ctx.fillText(formatNumber(n) + suffix, x + tw * 0.1, y + 56 * s);
    ctx.restore();
  });
}

function drawQueue(ctx: Ctx, palette: HeroPalette, labels: HeroLabels, p: PanelGeom, since: number, ripple: number): void {
  const s = p.s;
  drawScreenTag(ctx, palette, p.ix, p.y + 38 * s, labels.screens.live, 11 * s);
  setFont(ctx, 800, 18 * s, palette.fontBody);
  ctx.fillStyle = palette.text;
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillText(labels.queueTitle, p.ix, p.y + 80 * s);
  drawKpiTiles(ctx, palette, labels, p, since);
  QUEUE_ROWS.forEach((row, i) => {
    const k = expoOut(segment(since, 0.7 + i * (BEAT / 2), 1.1 + i * (BEAT / 2)));
    if (k <= 0) return;
    const y = p.y + 205 * s + i * 42 * s;
    ctx.save();
    ctx.globalAlpha *= k;
    if (i === 0 && ripple > 0) {
      ctx.fillStyle = withAlpha(palette.accent, 0.1);
      roundRect(ctx, p.ix - 8 * s, y - 18 * s, p.iw + 16 * s, 36 * s, 8 * s);
      ctx.fill();
    }
    ctx.fillStyle = delayColorIn(palette, row.delay);
    ctx.beginPath();
    ctx.arc(p.ix + 7 * s, y, 5 * s, 0, Math.PI * 2);
    ctx.fill();
    setFont(ctx, 800, 15 * s, palette.fontBody);
    ctx.fillStyle = palette.text;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(`${labels.routes[row.route]}\u3000${labels.stops[row.stop]}`, p.ix + 22 * s, y);
    ctx.textAlign = "right";
    ctx.fillStyle = delayColorIn(palette, row.delay);
    ctx.fillText(labels.delayShort(row.delay), p.ix + p.iw, y);
    ctx.restore();
  });
  // The map's own legend, docked here while the queue is showing.
  const ly = p.y + 345 * s;
  setFont(ctx, 700, 11 * s, palette.fontBody);
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  let cx = p.ix;
  const bands = [palette.delay.ok, palette.delay.mild, palette.delay.moderate, palette.delay.severe];
  labels.legendBands.forEach((band, i) => {
    ctx.fillStyle = bands[i];
    ctx.beginPath();
    ctx.arc(cx + 4 * s, ly, 4 * s, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = palette.text;
    ctx.fillText(band, cx + 12 * s, ly);
    cx += ctx.measureText(band).width + 22 * s;
  });
  setFont(ctx, 600, 10 * s, palette.fontBody);
  ctx.fillStyle = palette.muted;
  ctx.fillText(labels.legendDisclosure, p.ix, ly + 24 * s);
  if (ripple > 0) {
    ctx.strokeStyle = withAlpha(palette.accent, 1 - ripple);
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(p.ix + p.iw * 0.6, p.y + 205 * s, 6 * s + ripple * 36 * s, 0, Math.PI * 2);
    ctx.stroke();
  }
}

function drawTripDetail(ctx: Ctx, palette: HeroPalette, labels: HeroLabels, p: PanelGeom, frame: HeroFrame): void {
  const s = p.s;
  drawScreenTag(ctx, palette, p.ix, p.y + 38 * s, labels.screens.live, 11 * s);
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  setFont(ctx, 700, 13 * s, palette.fontBody);
  ctx.fillStyle = palette.muted;
  ctx.fillText(labels.tripHeading, p.ix, p.y + 80 * s);
  setFont(ctx, 800, 40 * s, palette.fontBody);
  ctx.fillStyle = delayColorIn(palette, SELECTED_TRIP.delay);
  ctx.fillText(labels.delayShort(SELECTED_TRIP.delay), p.ix, p.y + 124 * s);
  setFont(ctx, 800, 14 * s, palette.fontBody);
  ctx.fillStyle = palette.text;
  ctx.fillText(labels.tripChartTitle, p.ix, p.y + 168 * s);
  const box = tripChartBox(p);
  ctx.strokeStyle = palette.rule;
  ctx.lineWidth = 1;
  for (const v of [0, 3, 6]) {
    const y = box.y + box.h - (v / 7) * box.h;
    ctx.beginPath();
    ctx.moveTo(box.x, y);
    ctx.lineTo(box.x + box.w, y);
    ctx.stroke();
  }
  if (frame.trailMorph >= 1) drawTripChart(ctx, palette, labels, box, s);
  if (frame.tripNote > 0) {
    ctx.save();
    ctx.globalAlpha *= expoOut(frame.tripNote);
    setFont(ctx, 700, 13 * s, palette.fontBody);
    ctx.fillStyle = palette.text;
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    ctx.fillText(labels.tripNote, p.ix, p.y + 392 * s);
    ctx.restore();
  }
}

function drawHourly(ctx: Ctx, palette: HeroPalette, labels: HeroLabels, p: PanelGeom, frame: HeroFrame, rail: RailGeometry): void {
  const s = p.s;
  drawScreenTag(ctx, palette, p.ix, p.y + 38 * s, labels.screens.period, 11 * s);
  setFont(ctx, 800, 18 * s, palette.fontBody);
  ctx.fillStyle = palette.text;
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillText(labels.hourlyTitle, p.ix, p.y + 80 * s);
  if (frame.hourMorph >= 1) drawHourBars(ctx, palette, hourBarsBox(p), 1, frame.playback.hour, rail, s);
  if (frame.peakLabel > 0) {
    ctx.save();
    ctx.globalAlpha *= expoOut(frame.peakLabel);
    setFont(ctx, 900, 34 * s, palette.fontBody);
    ctx.fillStyle = palette.text;
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    const peak = labels.hourOfDay(PEAK_HOUR);
    ctx.fillText(peak, p.ix, p.y + 400 * s);
    const w = ctx.measureText(peak).width;
    setFont(ctx, 700, 12 * s, palette.fontBody);
    ctx.fillStyle = palette.muted;
    ctx.fillText(labels.peak, p.ix + w + 8 * s, p.y + 400 * s);
    ctx.restore();
  }
}

function drawOverview(ctx: Ctx, palette: HeroPalette, labels: HeroLabels, p: PanelGeom, since: number): void {
  const s = p.s;
  drawScreenTag(ctx, palette, p.ix, p.y + 38 * s, labels.screens.period, 11 * s);
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  setFont(ctx, 700, 12 * s, palette.fontBody);
  ctx.fillStyle = palette.muted;
  ctx.fillText(labels.overviewAverage, p.ix, p.y + 78 * s);
  const value = (OVERVIEW.networkAvg * expoOut(segment(since, 0.2, 1.0))).toFixed(1);
  setFont(ctx, 800, 64 * s, palette.fontBody);
  ctx.fillStyle = palette.text;
  ctx.fillText(value, p.ix - 2 * s, p.y + 146 * s);
  const vw = ctx.measureText(value).width;
  setFont(ctx, 800, 20 * s, palette.fontBody);
  ctx.fillText(labels.minuteUnit, p.ix + vw + 2 * s, p.y + 146 * s);
  setFont(ctx, 800, 13 * s, palette.fontBody);
  ctx.fillStyle = palette.delay.ok;
  ctx.fillText(labels.overviewChange, p.ix, p.y + 172 * s);
  const trend = OVERVIEW.trend;
  const reveal = segment(since, 0.4, 1.2);
  ctx.strokeStyle = palette.accent;
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  trend.forEach((v, i) => {
    if (i / (trend.length - 1) > reveal) return;
    const x = p.ix + p.iw * 0.56 + (i * p.iw * 0.42) / (trend.length - 1);
    const y = p.y + 128 * s - (v - 2) * 30 * s;
    if (i) ctx.lineTo(x, y);
    else ctx.moveTo(x, y);
  });
  ctx.stroke();
  setFont(ctx, 700, 12 * s, palette.fontBody);
  ctx.fillStyle = palette.muted;
  ctx.fillText(labels.overviewDelayedRoutes, p.ix, p.y + 212 * s);
  setFont(ctx, 800, 24 * s, palette.fontBody);
  ctx.fillStyle = palette.text;
  ctx.textAlign = "right";
  const delayed = Math.round(OVERVIEW.delayedRoutes * expoOut(segment(since, 0.3, 1.0)));
  ctx.fillText(`${delayed} / ${OVERVIEW.totalRoutes}`, p.ix + p.iw, p.y + 214 * s);
  ctx.textAlign = "left";
  setFont(ctx, 800, 15 * s, palette.fontBody);
  ctx.fillStyle = palette.text;
  ctx.fillText(labels.routesToCheck, p.ix, p.y + 262 * s);
  const worst = OVERVIEW.routesToCheck[0].delay;
  OVERVIEW.routesToCheck.forEach((row, i) => {
    const k = expoOut(segment(since, 0.9 + i * (BEAT / 3), 1.3 + i * (BEAT / 3)));
    if (k <= 0) return;
    const y = p.y + 294 * s + i * 34 * s;
    const barX = p.ix + p.iw * 0.28;
    const barW = p.iw * 0.5 * (row.delay / worst) * k;
    ctx.save();
    ctx.globalAlpha *= clamp01(k * 2);
    setFont(ctx, 700, 13 * s, palette.fontBody);
    ctx.fillStyle = palette.text;
    ctx.textBaseline = "middle";
    ctx.fillText(labels.routes[row.route], p.ix, y);
    ctx.fillStyle = delayColorIn(palette, row.delay);
    roundRect(ctx, barX, y - 6 * s, barW, 12 * s, 3 * s);
    ctx.fill();
    setFont(ctx, 800, 12 * s, palette.fontBody);
    ctx.fillStyle = palette.text;
    ctx.fillText(labels.minutes(row.delay), barX + barW + 6 * s, y);
    ctx.restore();
  });
}

/** Slides a section in from the right and out to the left, clipped to the panel. */
function withSection(ctx: Ctx, p: PanelGeom, section: PanelSection, draw: () => void): void {
  ctx.save();
  ctx.beginPath();
  ctx.rect(p.x, p.y, p.w, p.h);
  ctx.clip();
  ctx.globalAlpha *= expoOut(section.enter) * (1 - section.exit);
  ctx.translate((1 - expoOut(section.enter)) * p.w * 0.35 - section.exit * p.w * 0.35, 0);
  draw();
  ctx.restore();
}

export function drawPanel(ctx: Ctx, palette: HeroPalette, labels: HeroLabels, p: PanelGeom, frame: HeroFrame, rail: RailGeometry): void {
  if (frame.panelEnter <= 0) return;
  const shift = (1 - frame.panelEnter) * (p.w + 40);
  const moved: PanelGeom = { ...p, x: p.x + shift, ix: p.ix + shift };
  drawCard(ctx, palette, moved.x, moved.y, moved.w, moved.h, 14 * p.s);
  for (const section of frame.sections) {
    withSection(ctx, moved, section, () => {
      if (section.kind === "queue") drawQueue(ctx, palette, labels, moved, section.since, frame.queueRipple);
      else if (section.kind === "trip") drawTripDetail(ctx, palette, labels, moved, frame);
      else if (section.kind === "hourly") drawHourly(ctx, palette, labels, moved, frame, rail);
      else drawOverview(ctx, palette, labels, moved, section.since);
    });
  }
}

