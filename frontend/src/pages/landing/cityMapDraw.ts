import type { CityScene, VehiclePose } from "./cityMapScene";
import { drawVehicleIcon, MAKI_VIEWBOX_SIZE, type VehicleMode } from "./vehicleIcons";

export type RouteColors = { onTime: string; delayed: string };
export type VehicleDraw = {
  pose: VehiclePose;
  colorVar: "--accent" | "--color-warning";
  mode: VehicleMode;
};

/** Paints one frame of the schematic city scene: blocks, park, river, the
 *  two metro-style routes with their station dots, then every vehicle
 *  marker on top as an upright, mode-specific Maki glyph badge (never
 *  rotated to face its direction of travel -- see the per-vehicle comment
 *  below). Takes plain pixel `width`/`height` (already dpr-scaled by the
 *  caller via `ctx.setTransform`) and multiplies the scene's normalized [0,1]
 *  coordinates by them, so this function has no dependency on the actual
 *  canvas element -- callable against any 2D-context-shaped object, which
 *  is what makes it unit-testable with a plain recording stub. */
export function drawScene(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  scene: CityScene,
  vehicles: VehicleDraw[],
  colors: RouteColors,
): void {
  if (width <= 0 || height <= 0) return;
  ctx.clearRect(0, 0, width, height);

  ctx.fillStyle = "rgba(255,255,255,0.03)";
  ctx.fillRect(0, 0, width, height);

  ctx.fillStyle = "rgba(255,255,255,0.07)";
  for (const b of scene.blocks) {
    ctx.fillRect(b.x * width, b.y * height, b.w * width, b.h * height);
  }

  ctx.fillStyle = "rgba(120,200,150,0.14)";
  ctx.fillRect(scene.park.x * width, scene.park.y * height, scene.park.w * width, scene.park.h * height);

  const strokeScale = Math.max(width, height);
  ctx.strokeStyle = "rgba(120,170,220,0.22)";
  ctx.lineWidth = strokeScale * 0.02;
  ctx.beginPath();
  scene.river.forEach((p, i) => {
    const x = p.x * width;
    const y = p.y * height;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  const stationRadius = strokeScale * 0.006;
  for (const route of scene.routes) {
    const color = route.colorVar === "--accent" ? colors.onTime : colors.delayed;
    ctx.strokeStyle = color;
    ctx.lineWidth = strokeScale * 0.006;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.beginPath();
    route.points.forEach((p, i) => {
      const x = p.x * width;
      const y = p.y * height;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    ctx.fillStyle = color;
    for (const p of route.points) {
      ctx.beginPath();
      ctx.arc(p.x * width, p.y * height, stationRadius, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // Badge radius targets a fixed ~26-32px on-screen diameter (13-16px
  // radius) so each marker reads as a distinct vehicle glyph, not a dot --
  // clamped to that CSS-pixel range rather than left as a bare fraction of
  // `strokeScale`, since the hero canvas is full-bleed with no max-width and
  // `strokeScale` (the canvas's own width/height) grows unbounded with the
  // viewport. Re-tune this clamp (not the glyph fill below it) if the
  // marker ever needs to resize, since a smaller glyph inside the same badge
  // goes illegible before the badge itself looks wrong.
  const vehicleRadius = Math.min(Math.max(strokeScale * 0.02, 13), 16);
  // The glyph fills most of the badge, leaving a thin ring of the badge's
  // own color visible around it (like a real map app's vehicle pin).
  const glyphSize = vehicleRadius * 1.7;
  const glyphScale = glyphSize / MAKI_VIEWBOX_SIZE;

  for (const { pose, colorVar, mode } of vehicles) {
    const color = colorVar === "--accent" ? colors.onTime : colors.delayed;
    ctx.save();
    ctx.translate(pose.x * width, pose.y * height);
    // Deliberately no ctx.rotate(pose.angleRad) here: the glyph stays
    // upright regardless of travel direction, matching how real map apps
    // (Google Maps, Citymapper) render vehicle markers -- a rotated glyph
    // reads as broken, not "in motion."

    // Soft glow halo behind the badge -- a larger, low-alpha fill rather
    // than ctx.shadowBlur, which is both slower to paint every frame and
    // harder to fake in a unit test.
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.22;
    ctx.beginPath();
    ctx.arc(0, 0, vehicleRadius * 1.6, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;

    // Solid circular badge in the route's real status color.
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(0, 0, vehicleRadius, 0, Math.PI * 2);
    ctx.fill();

    // Bold white Maki glyph centered on the badge. A bold silhouette (full
    // white fill) stays legible at marker scale; the thin colored-line
    // details of an earlier attempt disappeared at this size.
    ctx.scale(glyphScale, glyphScale);
    ctx.translate(-MAKI_VIEWBOX_SIZE / 2, -MAKI_VIEWBOX_SIZE / 2);
    ctx.fillStyle = "#fff";
    ctx.beginPath();
    drawVehicleIcon(ctx, mode);
    ctx.fill();

    ctx.restore();
  }
}
