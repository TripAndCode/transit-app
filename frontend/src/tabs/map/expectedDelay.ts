import type { ForecastHeatmapCell } from "../../api/types";

/** Pools a route's 168-cell (7 dow x 24 hour) expected-delay heatmap
 *  (from useForecastHeatmap) down to one sample-weighted expected-delay
 *  value for a single hour, respecting the tab's active weekday/weekend
 *  filter. Mirrors pipeline/reports/forecast.py's _pooled() formula —
 *  a sample-weighted mean of per-bucket means equals the pooled mean.
 *  Returns null when no matching cell has any samples. */
export function expectedDelayForHour(
  cells: ForecastHeatmapCell[],
  hour: number,
  dowFilter: "all" | "weekday" | "weekend",
): number | null {
  const matching = cells.filter((c) => {
    if (c.hour !== hour) return false;
    if (dowFilter === "weekday") return c.dow >= 1 && c.dow <= 5;
    if (dowFilter === "weekend") return c.dow === 6 || c.dow === 7;
    return true;
  });
  const totalSamples = matching.reduce((sum, c) => sum + c.samples, 0);
  if (totalSamples === 0) return null;
  const weightedSum = matching.reduce(
    (sum, c) => sum + (c.expected_avg_min ?? 0) * c.samples,
    0,
  );
  return weightedSum / totalSamples;
}
