/** The fewest observations a figure rests on before it carries the few-data
 *  mark, for the tables whose responses name no floor of their own: the
 *  server's ranking floor (`RANKING_MIN_SAMPLES` in
 *  pipeline/reports/rankings.py), which the Routes table echoes as
 *  `reliable_min_samples` and the manual names as "fewer than 100". */
export const FEW_DATA_MIN_SAMPLES = 100;

/** Whether a count is under the floor. A missing count or floor marks
 *  nothing: no floor, no claim. */
export function isFewData(samples: unknown, floor: number | null | undefined): boolean {
  if (floor == null || samples == null) return false;
  const n = Number(samples);
  return Number.isFinite(n) && n < floor;
}
