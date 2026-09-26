// Canvas renderer for one frame of the landing hero's live-map sequence.
// Stateless: everything it draws is derived from the `HeroFrame` (what the
// timeline says is visible at time t), the static `HeroMap` geometry, the
// resolved theme palette, and pre-translated labels.

import {
  makeProjector,
  mixHex,
  withAlpha,
  type Projector,
} from "./heroMapMath";
import {
  WORLD_EXTENT,
  coastZ,
  type DistrictId,
  type HeroMap,
  type MapRoute,
  type Point2,
  type StationId,
  type StopTower,
} from "./heroMapScene";
import type { CaptionIndex, HeroFrame } from "./heroMapTimeline";
import { MAKI_VIEWBOX_SIZE, drawVehicleIcon, type VehicleMode } from "./vehicleIcons";

/** Theme tokens the renderer needs, resolved from CSS to `#rrggbb` strings
 *  (canvas cannot read `var()`), plus the font stacks. */
export type HeroPalette = {
  background: string;
  text: string;
  textMuted: string;
  /** On-time end of the delay ramp. */
  accent: string;
  accentStrong: string;
  /** Delayed end of the delay ramp. Deliberately the warning tone, not a
   *  danger red: the hero stays calm even where it shows the worst delay. */
  warning: string;
  fontBody: string;
  fontMono: string;
};

export type HeroLabels = {
  stations: Record<StationId, string>;
  districts: Record<DistrictId, string>;
  captions: Record<CaptionIndex, { title: string; body: string }>;
  calloutRoute: string;
  calloutWeekOverWeek: (minutes: number) => string;
  towerLabel: string;
  legendOnTime: string;
  legendDelayed: string;
  hud: readonly string[];
  delay: (minutes: number) => string;
};

/** Map-only tints with no theme meaning (land, water, streets). The hero is
 *  always rendered dark, so these are fixed rather than token-driven. */
const MAP_TINTS = {
  land: "#141a2a",
  water: "#0a1320",
  park: "#13261f",
  street: "#6a7fb0",
  coast: "#3d6f8e",
  district: "#8a9bc6",
  busRoute: "#7f93a8",
  vehicle: "#ffffff",
  panel: "#0b0e16",
} as const;

/** Below this width the headline spans the canvas, so the scene drops below
 *  it and every piece of canvas text (callout, tower label, captions, legend,
 *  HUD) is left out rather than drawn under the headline. */
const WIDE_LAYOUT_MIN_WIDTH = 900;

type Ctx = CanvasRenderingContext2D;
type Vec3 = [number, number, number];

const ground = (p: Point2, y = 0.02): Vec3 => [p[0], y, p[1]];

function tracePath(ctx: Ctx, project: Projector, pts: readonly Vec3[]): void {
  ctx.beginPath();
  let penDown = false;
  for (const p of pts) {
    const q = project(p[0], p[1], p[2]);
    if (!q) {
      penDown = false;
      continue;
    }
    if (penDown) ctx.lineTo(q[0], q[1]);
    else ctx.moveTo(q[0], q[1]);
    penDown = true;
  }
}

function stroke3(ctx: Ctx, project: Projector, pts: readonly Vec3[]): void {
  tracePath(ctx, project, pts);
  ctx.stroke();
}

function fillGround(ctx: Ctx, project: Projector, pts: readonly Point2[]): void {
  tracePath(ctx, project, pts.map((p) => ground(p, 0)));
  ctx.closePath();
  ctx.fill();
}

/** Three stacked additive strokes (wide/faint, medium, thin/bright) read as a
 *  glow without the per-stroke cost of `shadowBlur`. */
function glowStroke(ctx: Ctx, project: Projector, pts: readonly Vec3[], color: string, width: number, alpha: number): void {
  if (alpha <= 0) return;
  ctx.globalCompositeOperation = "lighter";
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = withAlpha(color, 0.12 * alpha);
  ctx.lineWidth = width * 6;
  stroke3(ctx, project, pts);
  ctx.strokeStyle = withAlpha(color, 0.3 * alpha);
  ctx.lineWidth = width * 2.4;
  stroke3(ctx, project, pts);
  ctx.strokeStyle = withAlpha(mixHex(color, "#ffffff", 0.45), 0.95 * alpha);
  ctx.lineWidth = width;
  stroke3(ctx, project, pts);
  ctx.globalCompositeOperation = "source-over";
}

function groundRing(ctx: Ctx, project: Projector, x: number, z: number, radius: number, color: string, alpha: number): void {
  if (alpha <= 0) return;
  const pts: Vec3[] = [];
  for (let k = 0; k <= 24; k++) {
    const th = (k / 24) * Math.PI * 2;
    pts.push([x + Math.cos(th) * radius, 0.02, z + Math.sin(th) * radius]);
  }
  ctx.strokeStyle = withAlpha(color, alpha);
  ctx.lineWidth = 1.2;
  stroke3(ctx, project, pts);
}

function drawIcon(ctx: Ctx, mode: VehicleMode, cx: number, cy: number, size: number, color: string): void {
  ctx.save();
  ctx.translate(cx - size / 2, cy - size / 2);
  ctx.scale(size / MAKI_VIEWBOX_SIZE, size / MAKI_VIEWBOX_SIZE);
  ctx.fillStyle = color;
  ctx.beginPath();
  drawVehicleIcon(ctx, mode);
  ctx.fill();
  ctx.restore();
}

function drawBaseMap(ctx: Ctx, project: Projector, map: HeroMap, reveal: number): void {
  if (reveal <= 0) return;
  const far = WORLD_EXTENT;
  ctx.fillStyle = withAlpha(MAP_TINTS.land, 0.9 * reveal);
  fillGround(ctx, project, [[-far, far], [far, far], ...[...map.coastline].reverse()]);
  ctx.fillStyle = withAlpha(MAP_TINTS.water, reveal);
  fillGround(ctx, project, [...map.coastline, [far, -far], [-far, -far]]);
  fillGround(ctx, project, map.riverBanks);
  ctx.fillStyle = withAlpha(MAP_TINTS.park, 0.9 * reveal);
  for (const park of map.parks) fillGround(ctx, project, park);

  ctx.strokeStyle = withAlpha(MAP_TINTS.coast, 0.5 * reveal);
  ctx.lineWidth = 1.2;
  stroke3(ctx, project, map.coastline.map((p) => ground(p, 0)));

  // Street grid, every fourth line a heavier arterial; cross streets stop at
  // the shore so the grid never runs out over the water.
  ctx.lineWidth = 1;
  for (let x = -70; x <= 70; x += 2.5) {
    ctx.strokeStyle = withAlpha(MAP_TINTS.street, (x % 10 === 0 ? 0.16 : 0.07) * reveal);
    stroke3(ctx, project, [[x, 0, coastZ(x) + 0.4], [x + 5, 0, 90]]);
  }
  for (let z = -4; z <= 90; z += 2.5) {
    ctx.strokeStyle = withAlpha(MAP_TINTS.street, (z % 10 === 0 ? 0.16 : 0.07) * reveal);
    let run: Vec3[] = [];
    for (let x = -72; x <= 72; x += 4) {
      if (z > coastZ(x) + 0.4) run.push([x + (z + 4) * 0.05, 0, z]);
      else if (run.length) {
        stroke3(ctx, project, run);
        run = [];
      }
    }
    if (run.length) stroke3(ctx, project, run);
  }
}

function drawDistricts(ctx: Ctx, project: Projector, map: HeroMap, labels: HeroLabels, palette: HeroPalette, height: number, reveal: number): void {
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const d of map.districts) {
    const q = project(d.x, 0, d.z);
    if (!q) continue;
    const size = Math.min(40, Math.max(6, (height * 1.2) / q[2]));
    ctx.font = `600 ${size}px ${palette.fontBody}`;
    ctx.fillStyle = withAlpha(MAP_TINTS.district, 0.16 * reveal);
    ctx.fillText(labels.districts[d.id], q[0], q[1]);
  }
  ctx.textBaseline = "alphabetic";
}

function routeColor(route: MapRoute, palette: HeroPalette): string {
  if (route.mode === "train") return palette.accentStrong;
  if (route.mode === "tram") return palette.accent;
  return MAP_TINTS.busRoute;
}

/** Draws the route up to `progress`, each short section tinted from its base
 *  color toward the delay ramp by that section's own average delay. */
function drawRoute(ctx: Ctx, project: Projector, route: MapRoute, progress: number, tint: number, palette: HeroPalette, width: number): void {
  const base = routeColor(route, palette);
  ctx.strokeStyle = withAlpha(base, 0.12);
  ctx.lineWidth = 1;
  stroke3(ctx, project, route.path.points.map((p) => ground(p)));
  if (progress <= 0) return;
  const sections = 60;
  const lineWidth = width * 0.0018 * (route.mode === "bus" ? 1 : route.mode === "tram" ? 1.1 : 1.5);
  for (let k = 0; k < sections; k++) {
    const a = k / sections;
    if (a >= progress) break;
    const b = Math.min((k + 1) / sections, progress);
    const delay = route.delayAt((a + b) / 2) * tint;
    const color = mixHex(base, palette.warning, (delay - 0.8) / 3);
    glowStroke(ctx, project, [ground(route.path.at(a)), ground(route.path.at(b))], color, lineWidth, 0.9);
  }
}

function drawVehicles(ctx: Ctx, project: Projector, map: HeroMap, t: number, alpha: number, width: number, height: number): void {
  if (alpha <= 0) return;
  map.routes.forEach((route, ri) => {
    const isRail = route.mode === "train";
    const count = isRail ? 2 : 3;
    const speed = isRail ? 9 : 3.4;
    const trailLength = isRail ? 5 : 2;
    for (let v = 0; v < count; v++) {
      const raw = ((t - 3.4) * speed) / route.path.length + v / count + ri * 0.17;
      const u = ((raw % 1) + 1) % 1;
      const trail: Vec3[] = [];
      for (let k = 0; k <= 8; k++) trail.push(ground(route.path.at(u - ((1 - k / 8) * trailLength) / route.path.length), 0.03));
      for (let k = 0; k < 8; k++) {
        glowStroke(ctx, project, [trail[k], trail[k + 1]], MAP_TINTS.vehicle, width * 0.0016, ((k + 1) / 8) * 0.6 * alpha);
      }
      const head = project(...trail[8]);
      if (head) {
        ctx.fillStyle = withAlpha(MAP_TINTS.vehicle, alpha);
        ctx.beginPath();
        ctx.arc(head[0], head[1], Math.min(4, Math.max(1.5, (height * 0.5) / head[2])), 0, Math.PI * 2);
        ctx.fill();
      }
    }
  });
}

function drawStations(ctx: Ctx, project: Projector, map: HeroMap, labels: HeroLabels, palette: HeroPalette, frame: HeroFrame, width: number, height: number): void {
  const alpha = frame.stationAlpha;
  if (alpha <= 0) return;
  for (const s of map.stations) {
    const q = project(s.x, 0.05, s.z);
    if (!q) continue;
    const size = Math.min(9, Math.max(3, (height * 0.9) / q[2])) * (s.major ? 1.4 : 1);
    ctx.fillStyle = withAlpha(palette.background, alpha);
    ctx.strokeStyle = withAlpha(MAP_TINTS.vehicle, 0.9 * alpha);
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.roundRect(q[0] - size / 2, q[1] - size / 2, size, size, 2);
    ctx.fill();
    ctx.stroke();
    if (frame.stationLabels) {
      const fontSize = Math.min(15, Math.max(9, width * 0.0105 * (s.major ? 1.2 : 1)));
      ctx.font = `${s.major ? 600 : 400} ${fontSize}px ${palette.fontBody}`;
      ctx.textAlign = "left";
      ctx.fillStyle = withAlpha(palette.text, 0.85 * alpha);
      ctx.fillText(labels.stations[s.id], q[0] + size, q[1] - size * 0.6);
    }
  }
}

function drawCallout(ctx: Ctx, project: Projector, map: HeroMap, labels: HeroLabels, palette: HeroPalette, frame: HeroFrame, width: number, height: number): void {
  const a = frame.calloutAlpha;
  if (a <= 0) return;
  const { x, z, delay, weekOverWeek } = map.hotspot;
  const q = project(x, 0.05, z);
  if (!q) return;
  groundRing(ctx, project, x, z, 1.6 + 0.2 * Math.sin(frame.t * 4), palette.warning, a);
  const boxX = q[0] + width * 0.03;
  const boxY = q[1] - height * 0.2;
  const boxW = Math.max(200, width * 0.19);
  ctx.strokeStyle = withAlpha(palette.warning, 0.7 * a);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(q[0], q[1]);
  ctx.lineTo(boxX, boxY + height * 0.08);
  ctx.stroke();
  ctx.fillStyle = withAlpha(MAP_TINTS.panel, 0.88 * a);
  ctx.strokeStyle = withAlpha(palette.warning, 0.5 * a);
  ctx.beginPath();
  ctx.roundRect(boxX, boxY - height * 0.02, boxW, height * 0.12, 6);
  ctx.fill();
  ctx.stroke();
  const small = Math.max(10, width * 0.0105);
  drawIcon(ctx, "bus", boxX + small * 1.5, boxY + height * 0.025, small * 1.3, withAlpha(palette.textMuted, a));
  ctx.textAlign = "left";
  ctx.font = `${small}px ${palette.fontBody}`;
  ctx.fillStyle = withAlpha(palette.textMuted, a);
  ctx.fillText(labels.calloutRoute, boxX + small * 2.6, boxY + height * 0.03);
  const big = Math.max(16, width * 0.02);
  ctx.font = `500 ${big}px ${palette.fontMono}`;
  ctx.fillStyle = withAlpha(palette.warning, a);
  const delayText = labels.delay(delay);
  ctx.fillText(delayText, boxX + small, boxY + height * 0.085);
  const delayWidth = ctx.measureText(delayText).width;
  ctx.font = `${small * 0.9}px ${palette.fontBody}`;
  ctx.fillStyle = withAlpha(palette.textMuted, a);
  ctx.fillText(labels.calloutWeekOverWeek(weekOverWeek), boxX + small * 2 + delayWidth, boxY + height * 0.083);
}

const towerHeight = (tower: StopTower): number => 0.4 + tower.delay * 1.5;

function drawTower(ctx: Ctx, project: Projector, tower: StopTower, h: number, color: string): void {
  const w = 0.55;
  const corners: Point2[] = [
    [tower.x - w, tower.z - w],
    [tower.x + w, tower.z - w],
    [tower.x + w, tower.z + w],
    [tower.x - w, tower.z + w],
  ];
  const faces = corners.map((p, i) => {
    const q = corners[(i + 1) % 4];
    return [project(p[0], 0, p[1]), project(q[0], 0, q[1]), project(q[0], h, q[1]), project(p[0], h, p[1])];
  });
  if (faces.some((f) => f.some((v) => !v))) return;
  const projected = faces as [number, number, number][][];
  // Painter's order: farthest face first.
  const order = projected
    .map((f, i) => ({ i, depth: f.reduce((sum, v) => sum + v[2], 0) }))
    .sort((m, n) => n.depth - m.depth);
  for (const { i } of order) {
    const f = projected[i];
    const g = ctx.createLinearGradient(0, f[0][1], 0, f[3][1]);
    g.addColorStop(0, withAlpha(color, 0.1));
    g.addColorStop(1, withAlpha(color, i % 2 ? 0.55 : 0.75));
    ctx.fillStyle = g;
    ctx.beginPath();
    f.forEach((v, k) => (k ? ctx.lineTo(v[0], v[1]) : ctx.moveTo(v[0], v[1])));
    ctx.closePath();
    ctx.fill();
  }
  const top = corners.map((p) => project(p[0], h, p[1])!);
  ctx.fillStyle = mixHex(color, "#ffffff", 0.35);
  ctx.beginPath();
  top.forEach((v, k) => (k ? ctx.lineTo(v[0], v[1]) : ctx.moveTo(v[0], v[1])));
  ctx.closePath();
  ctx.fill();
}

function drawTowers(ctx: Ctx, project: Projector, map: HeroMap, palette: HeroPalette, frame: HeroFrame, width: number): void {
  const visible = map.towers
    .map((tower, i) => ({ tower, i, growth: frame.towerGrowth(i), base: project(tower.x, 0, tower.z) }))
    .filter((v) => v.growth > 0 && v.base)
    .sort((a, b) => b.base![2] - a.base![2]);
  for (const { tower, growth } of visible) {
    const h = towerHeight(tower) * growth;
    const color = mixHex(palette.accent, palette.warning, (tower.delay - 0.8) / 4);
    drawTower(ctx, project, tower, h, color);
    if (tower.delay > 3.5) {
      const top = project(tower.x, h, tower.z);
      if (!top) continue;
      const r = width * 0.02;
      const g = ctx.createRadialGradient(top[0], top[1], 0, top[0], top[1], r);
      g.addColorStop(0, withAlpha(palette.warning, 0.4));
      g.addColorStop(1, withAlpha(palette.warning, 0));
      ctx.globalCompositeOperation = "lighter";
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(top[0], top[1], r, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalCompositeOperation = "source-over";
    }
  }
}

function drawTowerLabel(ctx: Ctx, project: Projector, map: HeroMap, labels: HeroLabels, palette: HeroPalette, frame: HeroFrame, width: number, height: number): void {
  const a = frame.towerLabelAlpha;
  if (a <= 0) return;
  const tower = map.tallestTower;
  const q = project(tower.x, towerHeight(tower) + 0.3, tower.z);
  if (!q) return;
  // The label leads up and right from the tower top, unless that would run
  // into the top edge (and the HUD there), in which case it leads down.
  const leadsUp = q[1] - height * 0.14 > height * 0.2;
  const lineY = q[1] + (leadsUp ? -1 : 1) * height * 0.06;
  const textX = q[0] + width * 0.042;
  const bigSize = Math.max(16, width * 0.02);
  const smallSize = Math.max(10, width * 0.0105);
  const delayText = labels.delay(tower.delay);
  ctx.font = `${smallSize}px ${palette.fontBody}`;
  const panelW = Math.max(ctx.measureText(labels.towerLabel).width, delayText.length * bigSize * 0.62) + smallSize * 1.4;
  ctx.fillStyle = withAlpha(MAP_TINTS.panel, 0.72 * a);
  ctx.beginPath();
  ctx.roundRect(textX - smallSize * 0.7, lineY - bigSize * 1.35, panelW, bigSize * 1.35 + smallSize * 2.1, 6);
  ctx.fill();
  ctx.strokeStyle = withAlpha(palette.warning, 0.8 * a);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(q[0], q[1]);
  ctx.lineTo(q[0] + width * 0.04, lineY);
  ctx.lineTo(textX - smallSize * 0.7, lineY);
  ctx.stroke();
  ctx.textAlign = "left";
  ctx.font = `500 ${bigSize}px ${palette.fontMono}`;
  ctx.fillStyle = withAlpha(palette.warning, a);
  ctx.fillText(delayText, textX, lineY - bigSize * 0.35);
  ctx.font = `${smallSize}px ${palette.fontBody}`;
  ctx.fillStyle = withAlpha(palette.text, a);
  ctx.fillText(labels.towerLabel, textX, lineY + smallSize * 1.4);
}

function drawCaption(ctx: Ctx, labels: HeroLabels, palette: HeroPalette, frame: HeroFrame, width: number, height: number): void {
  if (!frame.caption) return;
  const { index, alpha } = frame.caption;
  const { title, body } = labels.captions[index];
  const right = width * 0.95;
  const y = height * 0.82;
  ctx.globalAlpha = alpha;
  ctx.textAlign = "right";
  ctx.fillStyle = palette.warning;
  ctx.fillRect(right - width * 0.02, y - height * 0.075, width * 0.02, 2);
  ctx.fillStyle = palette.text;
  ctx.font = `600 ${Math.max(14, width * 0.015)}px ${palette.fontBody}`;
  ctx.fillText(title, right, y - height * 0.02);
  ctx.fillStyle = palette.textMuted;
  ctx.font = `${Math.max(11, width * 0.011)}px ${palette.fontBody}`;
  ctx.fillText(body, right, y + height * 0.035);
  ctx.globalAlpha = 1;
}

function drawLegend(ctx: Ctx, labels: HeroLabels, palette: HeroPalette, alpha: number, width: number, height: number): void {
  if (alpha <= 0) return;
  const w = width * 0.12;
  const x = width * 0.95 - w;
  const y = height * 0.92;
  const g = ctx.createLinearGradient(x, 0, x + w, 0);
  g.addColorStop(0, palette.accentStrong);
  g.addColorStop(1, palette.warning);
  ctx.globalAlpha = alpha;
  ctx.fillStyle = g;
  ctx.fillRect(x, y, w, 3);
  ctx.fillStyle = palette.textMuted;
  ctx.font = `${Math.max(10, width * 0.0095)}px ${palette.fontBody}`;
  ctx.textAlign = "left";
  ctx.fillText(labels.legendOnTime, x, y - 6);
  ctx.textAlign = "right";
  ctx.fillText(labels.legendDelayed, x + w, y - 6);
  ctx.globalAlpha = 1;
}

function drawHud(ctx: Ctx, labels: HeroLabels, palette: HeroPalette, t: number, width: number): void {
  const alpha = Math.min(1, Math.max(0, (t - 0.3) / 0.7));
  if (alpha <= 0) return;
  const margin = width * 0.025;
  const arm = width * 0.02;
  const right = width - margin;
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = withAlpha(palette.text, 0.4);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(right - arm, margin);
  ctx.lineTo(right, margin);
  ctx.lineTo(right, margin + arm);
  ctx.stroke();
  ctx.font = `${Math.max(10, width * 0.0095)}px ${palette.fontMono}`;
  ctx.textAlign = "right";
  labels.hud.forEach((line, i) => {
    ctx.fillStyle = withAlpha(palette.text, i === 0 ? 0.8 : 0.6);
    ctx.fillText(line, right - width * 0.012, margin + width * 0.02 + i * width * 0.015);
  });
  ctx.fillStyle = palette.warning;
  ctx.beginPath();
  ctx.arc(right - width * 0.012 - ctx.measureText(labels.hud[0] ?? "").width - 8, margin + width * 0.017, 2.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalAlpha = 1;
}

export function drawHeroFrame(
  ctx: Ctx,
  width: number,
  height: number,
  frame: HeroFrame,
  map: HeroMap,
  palette: HeroPalette,
  labels: HeroLabels,
): void {
  const wide = width >= WIDE_LAYOUT_MIN_WIDTH;
  ctx.fillStyle = palette.background;
  ctx.fillRect(0, 0, width, height);
  const project = wide
    ? makeProjector(width, height, frame.camera, width * 0.6)
    : makeProjector(width, height, frame.camera, width * 0.5, height * 0.72);

  drawBaseMap(ctx, project, map, frame.mapReveal);
  drawDistricts(ctx, project, map, labels, palette, height, frame.mapReveal);
  map.routes.forEach((route, i) => drawRoute(ctx, project, route, frame.routeProgress(i), frame.delayTint, palette, width));
  if (frame.pulseAlpha > 0) {
    map.stations.forEach((s, i) => {
      const phase = (frame.t * 0.7 + i * 0.29) % 1;
      groundRing(ctx, project, s.x, s.z, 0.4 + phase * 3.2, palette.accentStrong, (1 - phase) * 0.55 * frame.pulseAlpha);
    });
  }
  drawVehicles(ctx, project, map, frame.t, frame.vehicleAlpha, width, height);
  drawStations(ctx, project, map, labels, palette, frame, width, height);
  if (wide) drawCallout(ctx, project, map, labels, palette, frame, width, height);
  drawTowers(ctx, project, map, palette, frame, width);

  if (wide) {
    drawTowerLabel(ctx, project, map, labels, palette, frame, width, height);
    drawCaption(ctx, labels, palette, frame, width, height);
    drawLegend(ctx, labels, palette, frame.legendAlpha, width, height);
    drawHud(ctx, labels, palette, frame.t, width);
  }
}
