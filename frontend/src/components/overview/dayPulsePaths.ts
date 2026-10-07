import { delayRampVar } from "../../styles/tokens";

export const RIBBON_W = 960;
export const RIBBON_H = 160;
/** The ramp's ceiling (HEAT_RAMP.maxMin): a 9-minute hour is still "the top". */
const RIBBON_MAX_MIN = 5.5;
const PAD_TOP = 12;
const PAD_BOTTOM = 8;
/** Hours the gradient samples, spread across the day. */
const GRADIENT_HOURS = [3, 8, 13, 18, 23];

export type PulsePaths = { area: string; line: string };

/** One point per hour across the width; a null hour breaks the line and
 *  rests the area on the ground -- no invented zero drawn as a reading.
 *
 *  The area always has the same command sequence (a move to the ground, one
 *  line-to per hour, the closing edge), so a CSS `d` transition can
 *  interpolate it between two profiles; the line's moves follow the gaps, so
 *  it snaps when the gaps themselves change. */
export function pulsePaths(
  byHour: readonly (number | null)[],
  opts: { width?: number; height?: number; maxMin?: number } = {},
): PulsePaths {
  const { width = RIBBON_W, height = RIBBON_H, maxMin = RIBBON_MAX_MIN } = opts;
  if (!byHour.some((v) => v != null)) return { area: "", line: "" };
  const n = Math.max(1, byHour.length - 1);
  const yOf = (v: number) =>
    (height - PAD_BOTTOM - (Math.min(Math.max(v, 0), maxMin) / maxMin) * (height - PAD_TOP - PAD_BOTTOM)).toFixed(1);
  // Whole numbers print bare ("0", "960"), the rest to one decimal.
  const xOf = (i: number) => ((i / n) * width).toFixed(1).replace(/\.0$/, "");
  const line: string[] = [];
  const area: string[] = [`M0,${height}`];
  let pen = false;
  byHour.forEach((v, i) => {
    const x = xOf(i);
    if (v == null) {
      pen = false;
      area.push(`L${x},${height}`);
      return;
    }
    const y = yOf(v);
    line.push(`${pen ? "L" : "M"}${x},${y}`);
    area.push(`L${x},${y}`);
    pen = true;
  });
  area.push(`L${width},${height}`, `L0,${height}Z`);
  return { line: line.join(" "), area: area.join(" ") };
}

/** Gradient stops for the ribbon: five hours sampled onto the delay ramp, so
 *  the colour drifts across the day with the profile. Each stop sits at its
 *  hour's x, where `pulsePaths` draws that hour. A missing hour reads as no
 *  delay rather than as a gap -- a gradient has no way to draw one. */
export function pulseGradientStops(byHour: readonly (number | null)[]): { offset: number; color: string }[] {
  const n = Math.max(1, byHour.length - 1);
  return GRADIENT_HOURS.map((hour) => ({ offset: hour / n, color: delayRampVar(byHour[hour] ?? 0) }));
}
