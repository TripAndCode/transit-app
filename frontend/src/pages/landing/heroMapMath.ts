// Pure math for the landing hero: easing, a pinhole camera that projects
// world points onto the canvas, keyframed camera paths, and hex color
// blending. No canvas or DOM access, so all of it is unit-testable.

export type Camera = {
  x: number;
  y: number;
  z: number;
  /** Heading in radians; 0 looks toward +z, positive turns toward +x. */
  yaw: number;
  /** Look-down angle in radians; 0 is level, PI/2 is straight down. */
  pitch: number;
  /** Focal length as a multiple of the canvas height. */
  focal: number;
};

/** Screen x, screen y, view-space depth, and pixels per world unit there. */
type Projected = readonly [number, number, number, number];

export type Projector = (x: number, y: number, z: number) => Projected | null;

/** Points closer than this to the camera plane are dropped instead of being
 *  projected, so a segment passing behind the camera never flips to the
 *  opposite side of the screen. */
const NEAR_PLANE = 0.2;

export const clamp01 = (v: number): number => Math.min(1, Math.max(0, v));

export const lerp = (a: number, b: number, k: number): number => a + (b - a) * k;

/** Progress of `t` through the window [a, b], clamped to 0..1. */
export const segment = (t: number, a: number, b: number): number => clamp01((t - a) / (b - a));

export const expoOut = (x: number): number => (x >= 1 ? 1 : 1 - Math.pow(2, -10 * x));

export const expoIn = (x: number): number => (x <= 0 ? 0 : Math.pow(2, 10 * x - 10));

export const expoInOut = (x: number): number => {
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  return x < 0.5 ? Math.pow(2, 20 * x - 10) / 2 : (2 - Math.pow(2, -20 * x + 10)) / 2;
};

/** Overshoots past 1 before settling — for elements that "pop" in. */
export const backOut = (x: number, overshoot = 1.9): number => {
  const c = overshoot + 1;
  return 1 + c * Math.pow(x - 1, 3) + overshoot * Math.pow(x - 1, 2);
};

/** Gaussian bump: 1 at `center`, falling off over `width`. */
export const gaussianBump = (u: number, center: number, width: number): number => {
  const d = (u - center) / width;
  return Math.exp(-d * d);
};

/** `centerX`/`centerY` move the vanishing point off-center, so the scene can
 *  sit beside or below a text column rather than under it. */
export function makeProjector(
  width: number,
  height: number,
  cam: Camera,
  centerX = width / 2,
  centerY = height / 2,
): Projector {
  const cosYaw = Math.cos(cam.yaw);
  const sinYaw = Math.sin(cam.yaw);
  const cosPitch = Math.cos(cam.pitch);
  const sinPitch = Math.sin(cam.pitch);
  const focal = cam.focal * height;
  return (x, y, z) => {
    const dx = x - cam.x;
    const dy = y - cam.y;
    const dz = z - cam.z;
    const x1 = dx * cosYaw - dz * sinYaw;
    const z1 = dx * sinYaw + dz * cosYaw;
    const y2 = dy * cosPitch + z1 * sinPitch;
    const z2 = -dy * sinPitch + z1 * cosPitch;
    if (z2 < NEAR_PLANE) return null;
    const scale = focal / z2;
    return [centerX + x1 * scale, centerY - y2 * scale, z2, scale];
  };
}

/** Linear blend of two cameras; callers ease `k` themselves. */
export function mixCamera(a: Camera, b: Camera, k: number): Camera {
  return {
    x: lerp(a.x, b.x, k),
    y: lerp(a.y, b.y, k),
    z: lerp(a.z, b.z, k),
    yaw: lerp(a.yaw, b.yaw, k),
    pitch: lerp(a.pitch, b.pitch, k),
    focal: lerp(a.focal, b.focal, k),
  };
}

function hexChannels(hex: string): [number, number, number] {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as [number, number, number];
}

/** Blends two `#rrggbb` colors; `k` = 0 is `a`, 1 is `b`. */
export function mixHex(a: string, b: string, k: number): string {
  const ca = hexChannels(a);
  const cb = hexChannels(b);
  const kk = clamp01(k);
  return (
    "#" +
    ca
      .map((v, i) =>
        Math.round(lerp(v, cb[i], kk))
          .toString(16)
          .padStart(2, "0"),
      )
      .join("")
  );
}

export function withAlpha(hex: string, alpha: number): string {
  const [r, g, b] = hexChannels(hex);
  return `rgba(${r},${g},${b},${clamp01(alpha)})`;
}

export const isHexColor = (value: string): boolean => /^#[0-9a-f]{6}$/i.test(value);

/** Relative luminance (0 black – 1 white), for choosing light or dark tints. */
export function luminance(hex: string): number {
  const [r, g, b] = hexChannels(hex).map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
