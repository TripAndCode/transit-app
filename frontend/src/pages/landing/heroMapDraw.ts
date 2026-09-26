// Canvas renderer for one frame of the landing hero. Stateless: everything
// is derived from the `HeroFrame` (what the timeline says is on screen at
// time t), the static scene data, the resolved palette and the translated
// labels. The map layers mirror the operations map's real ones; the panel
// and the geometry the morphs fly between live in heroPanelDraw.

import { delayColorIn, drawCard, haloText, setFont, type Ctx, type HeroLabels, type HeroPalette } from "./heroCanvas";
import { backOut, clamp01, expoIn, expoInOut, expoOut, lerp, makeProjector, withAlpha, type Projector } from "./heroMapMath";
import {
  BASEMAP,
  FIRST_SERVICE_HOUR,
  HOURLY_MEANS,
  ROUTES,
  SELECTED_ROUTE,
  SELECTED_ROUTE_STOP_AVG,
  SELECTED_TRIP,
  TRIPS,
  TRIP_HISTORY,
  type Point2,
} from "./heroMapScene";
import type { HeroFrame } from "./heroMapTimeline";
import {
  drawHourBars,
  drawPanel,
  hourBarsBox,
  layoutFor,
  railGeometry,
  tripChartBox,
  tripChartPoint,
  type HeroLayout,
  type RailGeometry,
} from "./heroPanelDraw";

/** Marker radii the operations map uses: default, 5+ min late, selected. */
const DOT_RADIUS = { base: 6, severe: 9, selected: 11 } as const;
/** Dots closer than this many base radii merge into one count circle. */
const CLUSTER_DISTANCE = 3.2;
const TAU = Math.PI * 2;

function tracePath(ctx: Ctx, project: Projector, pts: readonly Point2[], close = false): boolean {
  ctx.beginPath();
  let drawn = 0;
  for (const [x, z] of pts) {
    const q = project(x, 0, z);
    if (!q) continue;
    if (drawn++) ctx.lineTo(q[0], q[1]);
    else ctx.moveTo(q[0], q[1]);
  }
  if (close) ctx.closePath();
  return drawn > 1;
}

function drawBasemap(ctx: Ctx, project: Projector, palette: HeroPalette, height: number): void {
  ctx.fillStyle = palette.water;
  if (tracePath(ctx, project, BASEMAP.sea, true)) ctx.fill();
  if (tracePath(ctx, project, BASEMAP.river, true)) ctx.fill();
  ctx.fillStyle = palette.park;
  for (const park of BASEMAP.parks) if (tracePath(ctx, project, park, true)) ctx.fill();
  ctx.strokeStyle = palette.road;
  ctx.lineWidth = Math.max(2, height * 0.004);
  for (const road of BASEMAP.roads) if (tracePath(ctx, project, road)) ctx.stroke();
  // Railways are part of the basemap, as they are on the app's map tiles.
  for (const route of ROUTES) {
    if (route.mode !== "train" || !tracePath(ctx, project, route.path.points)) continue;
    ctx.strokeStyle = palette.rail;
    ctx.lineWidth = 2.5;
    ctx.stroke();
    ctx.setLineDash([6, 6]);
    ctx.strokeStyle = palette.surface;
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

/** The selected route, colored stop-to-stop by each stop's average delay
 *  and spaced evenly by stop order; once drawn, a slow teal dash moves along
 *  it to show direction, as on the operations map. */
function drawSelectedRoute(ctx: Ctx, project: Projector, palette: HeroPalette, frame: HeroFrame): void {
  const k = frame.routeReveal;
  if (k <= 0) return;
  const path = ROUTES[SELECTED_ROUTE].path;
  const stops = SELECTED_ROUTE_STOP_AVG.length;
  ctx.lineCap = "round";
  ctx.lineWidth = 5;
  for (let i = 0; i < stops - 1; i++) {
    const u0 = i / (stops - 1);
    if (u0 >= k) break;
    const u1 = Math.min((i + 1) / (stops - 1), k);
    const steps = 10;
    for (let j = 0; j < steps; j++) {
      const a = path.at(lerp(u0, u1, j / steps));
      const b = path.at(lerp(u0, u1, (j + 1) / steps));
      const qa = project(a[0], 0, a[1]);
      const qb = project(b[0], 0, b[1]);
      if (!qa || !qb) continue;
      ctx.strokeStyle = delayColorIn(palette, lerp(SELECTED_ROUTE_STOP_AVG[i], SELECTED_ROUTE_STOP_AVG[i + 1], (j + 0.5) / steps));
      ctx.beginPath();
      ctx.moveTo(qa[0], qa[1]);
      ctx.lineTo(qb[0], qb[1]);
      ctx.stroke();
    }
  }
  if (k >= 1 && tracePath(ctx, project, path.points)) {
    ctx.save();
    ctx.setLineDash([10, 14]);
    ctx.lineDashOffset = -frame.t * 40;
    ctx.strokeStyle = palette.accent;
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.restore();
  }
}

/** The selected trip's reported stops: a teal trail with "›" direction
 *  marks and delay-colored stop circles, the latest one larger. */
function drawTrail(ctx: Ctx, project: Projector, palette: HeroPalette, labels: HeroLabels, frame: HeroFrame): void {
  if (frame.trailReveal <= 0) return;
  const path = ROUTES[SELECTED_ROUTE].path;
  const shown = TRIP_HISTORY.slice(0, Math.ceil(frame.trailReveal * TRIP_HISTORY.length));
  const pts = shown.map((h) => ({ h, q: project(...toGround(path.at(h.u))) })).filter((p) => p.q);
  ctx.save();
  ctx.globalAlpha *= frame.trailMorph > 0 ? 0.35 : 1;
  ctx.strokeStyle = palette.accent;
  ctx.lineWidth = 4;
  ctx.lineCap = "round";
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p.q![0], p.q![1]) : ctx.moveTo(p.q![0], p.q![1])));
  ctx.stroke();
  setFont(ctx, 900, 16, palette.fontBody);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i].q!;
    const b = pts[i + 1].q!;
    ctx.save();
    ctx.translate((a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
    ctx.rotate(Math.atan2(b[1] - a[1], b[0] - a[0]));
    ctx.fillStyle = palette.accent;
    ctx.fillText("›", 0, -1);
    ctx.restore();
  }
  pts.forEach((p, i) => {
    const latest = i === TRIP_HISTORY.length - 1;
    const r = latest ? 8 : 5;
    ctx.fillStyle = delayColorIn(palette, p.h.delay);
    ctx.strokeStyle = palette.surface;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(p.q![0], p.q![1], r, 0, TAU);
    ctx.fill();
    ctx.stroke();
    setFont(ctx, 700, 12, palette.fontBody);
    ctx.textAlign = "left";
    haloText(ctx, palette, `${labels.stops[p.h.stop]} +${p.h.delay}`, p.q![0] + r + 5, p.q![1]);
  });
  ctx.restore();
}

const toGround = ([x, z]: Point2): [number, number, number] => [x, 0, z];
const bump = (u: number, c: number, w: number) => Math.exp(-(((u - c) / w) ** 2));

/** Day playback: stop circles colored by the hour's severity. */
function drawPlaybackStops(ctx: Ctx, project: Projector, palette: HeroPalette, frame: HeroFrame): void {
  const a = frame.playback.stopsAlpha;
  if (a <= 0) return;
  const hourIndex = Math.min(HOURLY_MEANS.length - 1, Math.max(0, Math.floor(frame.playback.hour - FIRST_SERVICE_HOUR)));
  const mean = HOURLY_MEANS[hourIndex];
  ctx.save();
  ctx.globalAlpha *= a;
  for (const [routeKey, stopCount, weight] of [["bus12", 7, 1], ["tram3", 8, 0.7], ["bus7", 7, 1]] as const) {
    const path = ROUTES.find((r) => r.key === routeKey)!.path;
    for (let i = 0; i < stopCount; i++) {
      const u = i / (stopCount - 1);
      const q = project(...toGround(path.at(u)));
      if (!q) continue;
      const d = mean * (0.35 + 1.1 * bump(u, 0.5, 0.25)) * weight;
      ctx.fillStyle = delayColorIn(palette, d);
      ctx.strokeStyle = palette.surface;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(q[0], q[1], d >= 5 ? DOT_RADIUS.severe : DOT_RADIUS.base, 0, TAU);
      ctx.fill();
      ctx.stroke();
    }
  }
  ctx.restore();
}

/** Trip dots at their latest reported stop, merged into count circles when
 *  zoomed out; the refresh blinks them and they reappear one stop on. */
function drawTrips(ctx: Ctx, project: Projector, palette: HeroPalette, frame: HeroFrame): void {
  if (frame.tripsBlinking || frame.tripsAlpha <= 0) return;
  const pop = frame.tripsAtNextStop && frame.tripPop < 1 ? backOut(frame.tripPop) : 1;
  const placed = TRIPS.map((tp) => {
    const q = project(...toGround(ROUTES[tp.route].path.at(frame.tripsAtNextStop ? tp.nextU : tp.u)));
    return q ? { tp, x: q[0], y: q[1], appear: frame.tripAppear(tp.id) } : null;
  }).filter((p): p is NonNullable<typeof p> => p !== null && p.appear > 0);
  type Placed = (typeof placed)[number];
  const groups: { x: number; y: number; members: Placed[] }[] = [];
  for (const p of placed) {
    const near = frame.zoom < 0.5 ? groups.find((g) => Math.hypot(g.x - p.x, g.y - p.y) < DOT_RADIUS.base * CLUSTER_DISTANCE) : undefined;
    if (near) near.members.push(p);
    else groups.push({ x: p.x, y: p.y, members: [p] });
  }
  ctx.save();
  ctx.globalAlpha *= frame.tripsAlpha;
  const baseAlpha = ctx.globalAlpha;
  for (const g of groups) {
    const appear = Math.min(...g.members.map((m) => m.appear));
    const scale = (appear < 1 ? backOut(appear) : 1) * pop;
    if (g.members.length > 1) {
      const mean = g.members.reduce((s, m) => s + m.tp.delay, 0) / g.members.length;
      const r = 11 * scale;
      ctx.fillStyle = delayColorIn(palette, mean);
      ctx.strokeStyle = palette.surface;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(g.x, g.y, r, 0, TAU);
      ctx.fill();
      ctx.stroke();
      setFont(ctx, 800, 12, palette.fontBody);
      ctx.fillStyle = palette.surface;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String(g.members.length), g.x, g.y + 0.5);
      continue;
    }
    const { tp } = g.members[0];
    const selected = frame.tripSelected && tp.id === SELECTED_TRIP.id;
    const onRoute = tp.route === SELECTED_ROUTE;
    ctx.globalAlpha = baseAlpha * (frame.routeReveal > 0.5 && !onRoute ? 0.35 : 1);
    const r = (selected ? DOT_RADIUS.selected : tp.delay >= 5 ? DOT_RADIUS.severe : DOT_RADIUS.base) * scale;
    ctx.fillStyle = delayColorIn(palette, tp.delay);
    ctx.strokeStyle = palette.surface;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(g.x, g.y, Math.max(0, r), 0, TAU);
    ctx.fill();
    ctx.stroke();
    if (selected) {
      ctx.strokeStyle = palette.accent;
      ctx.beginPath();
      ctx.arc(g.x, g.y, r + 4, 0, TAU);
      ctx.stroke();
    }
    if (frame.zoom > 0.5 && (tp.delay >= 3 || selected)) {
      setFont(ctx, 700, 12, palette.fontBody);
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      haloText(ctx, palette, `${tp.time} +${Math.round(tp.delay)}`, g.x + r + 5, g.y);
    }
  }
  ctx.restore();
}

function drawRail(ctx: Ctx, palette: HeroPalette, labels: HeroLabels, rail: RailGeometry, frame: HeroFrame, height: number): void {
  const a = frame.playback.railAlpha;
  if (a <= 0) return;
  const hour = frame.playback.hour;
  const y = rail.y + (1 - expoOut(a)) * 16;
  ctx.save();
  ctx.globalAlpha *= a;
  setFont(ctx, 800, Math.min(48, height * 0.065), palette.fontMono);
  ctx.fillStyle = palette.text;
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillText(labels.clock(Math.min(23, Math.floor(hour))), rail.x, y - 12);
  drawCard(ctx, palette, rail.x, y, rail.w, rail.h, 10);
  ctx.fillStyle = palette.text;
  ctx.beginPath();
  ctx.moveTo(rail.x + rail.pad, y + rail.h * 0.3);
  ctx.lineTo(rail.x + rail.pad + rail.h * 0.32, y + rail.h * 0.5);
  ctx.lineTo(rail.x + rail.pad, y + rail.h * 0.7);
  ctx.fill();
  HOURLY_MEANS.forEach((v, i) => {
    ctx.fillStyle = delayColorIn(palette, v);
    ctx.beginPath();
    ctx.roundRect(rail.bx + i * rail.segW + 1, y + rail.h * 0.42, rail.segW - 2, rail.h * 0.16, 2);
    ctx.fill();
  });
  const px = rail.bx + clamp01((hour - FIRST_SERVICE_HOUR) / (24 - FIRST_SERVICE_HOUR)) * rail.bw;
  ctx.fillStyle = palette.text;
  ctx.beginPath();
  ctx.arc(px, y + rail.h * 0.5, rail.h * 0.16, 0, TAU);
  ctx.fill();
  setFont(ctx, 700, 11, palette.fontBody);
  ctx.fillStyle = palette.muted;
  ctx.textBaseline = "middle";
  ctx.fillText(labels.playback, rail.bx, y + rail.h * 0.22);
  ctx.textAlign = "right";
  ctx.fillStyle = palette.text;
  ctx.fillText(labels.playbackSpeed, rail.x + rail.w - rail.pad, y + rail.h * 0.5);
  ctx.restore();
}

function drawRefreshChip(ctx: Ctx, palette: HeroPalette, labels: HeroLabels, layout: HeroLayout, frame: HeroFrame, height: number): void {
  const a = frame.refreshChip;
  if (a <= 0) return;
  setFont(ctx, 700, 12, palette.fontBody);
  const w = ctx.measureText(labels.refresh).width + 44;
  const h = 32;
  const x = layout.focusX - w / 2;
  const y = height * 0.08;
  drawCard(ctx, palette, x, y, w, h, h / 2, a);
  ctx.save();
  ctx.globalAlpha *= a;
  ctx.fillStyle = palette.accent;
  ctx.beginPath();
  ctx.arc(x + 17, y + h / 2, 4, 0, TAU);
  ctx.fill();
  ctx.fillStyle = palette.text;
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText(labels.refresh, x + 28, y + h / 2);
  ctx.restore();
}

/** Numbered caption naming the screen being shown, revealed through a mask. */
function drawCaption(ctx: Ctx, palette: HeroPalette, labels: HeroLabels, layout: HeroLayout, frame: HeroFrame, height: number): void {
  const c = frame.caption;
  if (!c) return;
  const k = expoOut(c.enter);
  const out = expoIn(c.exit);
  const px = Math.min(24, height * 0.032);
  const { x, y } = layout.caption;
  const text = labels.captions[c.index];
  setFont(ctx, 800, px, palette.fontBody);
  const w = ctx.measureText(text).width + px * 2.4;
  ctx.save();
  ctx.beginPath();
  ctx.rect(x + w * out, y - px * 2, w * (k - out), px * 3);
  ctx.clip();
  setFont(ctx, 700, px * 0.45, palette.fontMono);
  ctx.fillStyle = palette.accent;
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillText(`0${c.index + 1}`, x, y - px * 1.05);
  ctx.fillRect(x + px * 1.1, y - px * 1.2, px * 1.6 * k, 2);
  setFont(ctx, 800, px, palette.fontBody);
  ctx.fillStyle = palette.text;
  ctx.fillText(text, x, y);
  ctx.restore();
}

/** The trip's reported stops lift off the map and land as the trip panel's
 *  per-stop chart — the same four readings, the same screen of the app. */
function drawTrailMorph(ctx: Ctx, project: Projector, palette: HeroPalette, frame: HeroFrame, layout: HeroLayout, height: number): void {
  const k = frame.trailMorph;
  if (!layout.panel || k <= 0 || k >= 1) return;
  const box = tripChartBox(layout.panel);
  const path = ROUTES[SELECTED_ROUTE].path;
  const pts = TRIP_HISTORY.map((h, i) => {
    const q = project(...toGround(path.at(h.u)));
    const [tx, ty] = tripChartPoint(box, i);
    const kk = expoInOut(clamp01(k * 1.35 - i * 0.1));
    return { x: lerp(q ? q[0] : tx, tx, kk), y: lerp(q ? q[1] : ty, ty, kk) - Math.sin(Math.PI * kk) * height * 0.12, d: h.delay, kk };
  });
  ctx.strokeStyle = palette.accent;
  ctx.lineWidth = 3.5;
  ctx.lineCap = "round";
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
  ctx.stroke();
  for (const p of pts) {
    ctx.fillStyle = delayColorIn(palette, p.d);
    ctx.strokeStyle = palette.surface;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(p.x, p.y, lerp(6, 5 * layout.panel.s, p.kk), 0, TAU);
    ctx.fill();
    ctx.stroke();
  }
}

function drawFrameMarks(ctx: Ctx, palette: HeroPalette, labels: HeroLabels, width: number, height: number): void {
  const m = Math.max(14, width * 0.016);
  const arm = Math.max(10, width * 0.009);
  ctx.strokeStyle = withAlpha(palette.text, 0.35);
  ctx.lineWidth = 1;
  for (const [x, y, sx, sy] of [[m, m, 1, 1], [width - m, m, -1, 1], [m, height - m, 1, -1], [width - m, height - m, -1, -1]]) {
    ctx.beginPath();
    ctx.moveTo(x + sx * arm, y);
    ctx.lineTo(x, y);
    ctx.lineTo(x, y + sy * arm);
    ctx.stroke();
  }
  setFont(ctx, 600, 10, palette.fontBody);
  ctx.fillStyle = withAlpha(palette.text, 0.5);
  ctx.textAlign = "right";
  ctx.textBaseline = "alphabetic";
  ctx.fillText(labels.sampleNotice, width - m - arm, height - m - 3);
}

export function drawHeroFrame(ctx: Ctx, width: number, height: number, frame: HeroFrame, palette: HeroPalette, labels: HeroLabels): void {
  const layout = layoutFor(width, height);
  const rail = railGeometry(layout.rail);
  ctx.fillStyle = palette.land;
  ctx.fillRect(0, 0, width, height);
  const project = makeProjector(width, height, frame.camera, layout.focusX, layout.focusY);
  drawBasemap(ctx, project, palette, height);
  drawSelectedRoute(ctx, project, palette, frame);
  drawTrail(ctx, project, palette, labels, frame);
  drawPlaybackStops(ctx, project, palette, frame);
  drawTrips(ctx, project, palette, frame);
  drawRail(ctx, palette, labels, rail, frame, height);
  drawRefreshChip(ctx, palette, labels, layout, frame, height);
  drawCaption(ctx, palette, labels, layout, frame, height);
  if (layout.panel) {
    drawPanel(ctx, palette, labels, layout.panel, frame, rail);
    drawTrailMorph(ctx, project, palette, frame, layout, height);
    if (frame.hourMorph > 0 && frame.hourMorph < 1) {
      drawHourBars(ctx, palette, hourBarsBox(layout.panel), frame.hourMorph, frame.playback.hour, rail, layout.panel.s);
    }
  }
  drawFrameMarks(ctx, palette, labels, width, height);
}
