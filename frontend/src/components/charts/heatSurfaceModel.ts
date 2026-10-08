import { LOW_CONFIDENCE_SAMPLES, bandOf, type Band } from "../../api/types";
import { DELAY_THRESHOLDS } from "../../styles/tokens";
import { isoDow } from "./trendFocus";

export const HOURS = 24;
export const DOWS = 7;
/** Opacity the cells outside the hovered row and column drop to. */
export const SURFACE_DIM = 0.55;
/** Resting opacity of a cell backed by few observations: faint, but above
 *  SURFACE_DIM, so a hover still visibly keeps its row and column. */
export const THIN_OPACITY = 0.75;

/** `[dowIndex 0 = Monday … 6 = Sunday][hour]`, null where nothing is known. */
export type Surface = (number | null)[][];
export type Profile = "hourly" | "banded";
/** `[dowIndex][hour]`: drawn faint because too few observations back it. */
export type Thin = boolean[][];

export function emptySurface(): Surface {
  return Array.from({ length: DOWS }, () => Array.from({ length: HOURS }, () => null));
}

type HourlyCell = { date: string; hour: number; avg_min: number | null; samples: number; sum_delay_sec?: number | null };
type BandCell = { dow: number; band: Band; expected_avg_min: number | null; low_confidence?: boolean };

/** Delay seconds and samples per (weekday, hour), pooled over the range. A
 *  cell with no samples or no average contributes nothing -- it is absence,
 *  not zero. */
function pooled(cells: readonly HourlyCell[]): { sec: number; n: number }[][] {
  const totals = Array.from({ length: DOWS }, () => Array.from({ length: HOURS }, () => ({ sec: 0, n: 0 })));
  for (const c of cells) {
    if (c.avg_min == null || !(c.samples > 0) || !Number.isInteger(c.hour) || c.hour < 0 || c.hour >= HOURS) continue;
    const bucket = totals[isoDow(c.date) - 1][c.hour];
    bucket.sec += c.sum_delay_sec ?? c.avg_min * 60 * c.samples;
    bucket.n += c.samples;
  }
  return totals;
}

/** Mean delay per (weekday, hour) over the range, weighted by samples so a
 *  busy Monday does not count the same as a quiet one, and which of those
 *  cells rest on too few samples to trust -- both from one pass. */
export function observedProfile(cells: readonly HourlyCell[]): { surface: Surface; thin: Thin } {
  const totals = pooled(cells);
  return {
    surface: totals.map((row) => row.map((b) => (b.n > 0 ? b.sec / b.n / 60 : null))),
    thin: totals.map((row) => row.map((b) => b.n > 0 && b.n < LOW_CONFIDENCE_SAMPLES)),
  };
}

export function observedSurface(cells: readonly HourlyCell[]): Surface {
  return observedProfile(cells).surface;
}

function eachBandHour(grid: readonly BandCell[], paint: (cell: BandCell, d: number, h: number) => void): void {
  for (const cell of grid) {
    if (!Number.isInteger(cell.dow) || cell.dow < 1 || cell.dow > DOWS) continue;
    for (let h = 0; h < HOURS; h += 1) if (bandOf(h) === cell.band) paint(cell, cell.dow - 1, h);
  }
}

/** The same range averaged by time band: the band grid is five bands wide
 *  and the surface 24 hours wide, so a band's average is drawn across its
 *  hours (`bandOf`) -- only those `observed` has a value for, so an hour no
 *  trip ran in stays empty in both profiles. */
export function bandSurface(grid: readonly BandCell[], observed: Surface): Surface {
  const s = emptySurface();
  eachBandHour(grid, (cell, d, h) => {
    if (cell.expected_avg_min != null && observed[d][h] != null) s[d][h] = cell.expected_avg_min;
  });
  return s;
}

/** A low-confidence band's hours, at the same observed hours `bandSurface`
 *  paints: an empty cell is empty, not faint. */
export function bandThin(grid: readonly BandCell[], observed: Surface): Thin {
  const thin = Array.from({ length: DOWS }, () => Array.from({ length: HOURS }, () => false));
  eachBandHour(grid, (cell, d, h) => {
    if (cell.low_confidence && observed[d][h] != null) thin[d][h] = true;
  });
  return thin;
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
 *  carry; rendered once into a <style> by HeatSurface. The rule outranks
 *  `.focus-dim-opacity`, so it takes the lowest of its own dim, a linked
 *  chart's (--focus-dim) and the cell's resting opacity (--mark-opacity). */
export function heatSurfaceDimRules(dim: number = SURFACE_DIM): string {
  const opacity = `min(var(--focus-dim),var(--mark-opacity),${String(dim).replace(/^0\./, ".")})`;
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
