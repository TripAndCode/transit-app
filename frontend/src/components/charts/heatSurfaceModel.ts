import { bandOf, type Band } from "../../api/types";
import { DELAY_THRESHOLDS } from "../../styles/tokens";
import { isoDow } from "./trendFocus";

export const HOURS = 24;
export const DOWS = 7;
/** Opacity the cells outside the hovered row and column drop to. */
export const SURFACE_DIM = 0.55;

/** `[dowIndex 0 = Monday … 6 = Sunday][hour]`, null where nothing is known. */
export type Surface = (number | null)[][];
export type Profile = "observed" | "forecast";

export function emptySurface(): Surface {
  return Array.from({ length: DOWS }, () => Array.from({ length: HOURS }, () => null));
}

/** Mean delay per (weekday, hour) over the range, weighted by samples so a
 *  busy Monday does not count the same as a quiet one. A cell with no
 *  samples or no average contributes nothing -- it is absence, not zero. */
export function observedSurface(
  cells: readonly { date: string; hour: number; avg_min: number | null; samples: number; sum_delay_sec?: number | null }[],
): Surface {
  const totals = Array.from({ length: DOWS }, () => Array.from({ length: HOURS }, () => ({ sec: 0, n: 0 })));
  for (const c of cells) {
    if (c.avg_min == null || !(c.samples > 0) || !Number.isInteger(c.hour) || c.hour < 0 || c.hour >= HOURS) continue;
    const bucket = totals[isoDow(c.date) - 1][c.hour];
    bucket.sec += c.sum_delay_sec ?? c.avg_min * 60 * c.samples;
    bucket.n += c.samples;
  }
  return totals.map((row) => row.map((b) => (b.n > 0 ? b.sec / b.n / 60 : null)));
}

/** The forecast grid is five bands wide; the surface is 24 hours wide, so a
 *  band's expected delay is drawn across each of its hours (`bandOf`). */
export function forecastSurface(grid: readonly { dow: number; band: Band; expected_avg_min: number | null }[]): Surface {
  const s = emptySurface();
  for (const cell of grid) {
    if (cell.expected_avg_min == null || !Number.isInteger(cell.dow) || cell.dow < 1 || cell.dow > DOWS) continue;
    for (let h = 0; h < HOURS; h += 1) if (bandOf(h) === cell.band) s[cell.dow - 1][h] = cell.expected_avg_min;
  }
  return s;
}

export function surfaceHasData(surface: Surface): boolean {
  return surface.some((row) => row.some((v) => v != null));
}

/** Inset depth by magnitude: the moderate and severe thresholds the rest of
 *  the product draws as outlines, here as an inset ring that reads as a
 *  pressed cell. Paint only -- a border would move the grid. */
export function ringFor(minutes: number | null): "0px" | "1px" | "2px" {
  if (minutes == null) return "0px";
  if (minutes >= DELAY_THRESHOLDS.severe) return "2px";
  if (minutes >= DELAY_THRESHOLDS.moderate) return "1px";
  return "0px";
}

/** One rule per (hour, weekday): with that pair on the grid, every cell in
 *  neither that column nor that row recedes. Generated rather than written,
 *  so the selector shape cannot drift from the data attributes the cells
 *  carry; rendered once into a <style> by HeatSurface. */
export function heatSurfaceDimRules(dim: number = SURFACE_DIM): string {
  const opacity = String(dim).replace(/^0\./, ".");
  const rules: string[] = [];
  for (let d = 1; d <= DOWS; d += 1) {
    for (let h = 0; h < HOURS; h += 1) {
      rules.push(
        `.heat-surface[data-focus-hour="${h}"][data-focus-dow="${d}"] .heat-surface__cell:not([data-hour="${h}"]):not([data-dow="${d}"]){opacity:${opacity}}`,
      );
    }
  }
  return rules.join("\n");
}
