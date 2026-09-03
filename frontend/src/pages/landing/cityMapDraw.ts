import type { CityScene, VehiclePose } from "./cityMapScene";

export type RouteColors = { onTime: string; delayed: string };
export type VehicleDraw = { pose: VehiclePose; colorVar: "--accent" | "--color-warning" };

/** Paints one frame of the schematic city scene: blocks, park, river, the
 *  two metro-style routes with their station dots, then every vehicle
 *  marker on top, oriented along its direction of travel. Takes plain
 *  pixel `width`/`height` (already dpr-scaled by the caller via
 *  `ctx.setTransform`) and multiplies the scene's normalized [0,1]
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

  const vehicleRadius = strokeScale * 0.011;
  for (const { pose, colorVar } of vehicles) {
    const color = colorVar === "--accent" ? colors.onTime : colors.delayed;
    ctx.save();
    ctx.translate(pose.x * width, pose.y * height);
    ctx.rotate(pose.angleRad);

    // Soft glow halo behind the body -- a larger, low-alpha fill rather than
    // ctx.shadowBlur, which is both slower to paint every frame and harder
    // to fake in a unit test.
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.22;
    ctx.beginPath();
    ctx.arc(0, 0, vehicleRadius * 2.4, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;

    // Rounded body, elongated along the direction of travel (already
    // rotated into place above).
    ctx.beginPath();
    ctx.ellipse(0, 0, vehicleRadius * 1.5, vehicleRadius, 0, 0, Math.PI * 2);
    ctx.fill();

    // Window pattern: deliberately mode-agnostic (not a literal bus/train
    // shape) -- just enough detail to read as "a vehicle" at hero scale.
    ctx.fillStyle = "rgba(255,255,255,0.85)";
    for (const dx of [-0.5, 0, 0.5]) {
      ctx.beginPath();
      ctx.arc(dx * vehicleRadius, 0, vehicleRadius * 0.22, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.restore();
  }
}
