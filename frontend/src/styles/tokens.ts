// Severity ramp for delays (minutes -> color). Calm at the low end so the
// usual data isn't visually loud; severe (>10 min) is a true red so the
// genuinely problematic stops pop without ambiguity.
export const DELAY_RAMP = {
  ok: "#8fb88f",       // < 2 min   sage
  mild: "#d4b878",     // 2 – 5 min sand
  moderate: "#e07a3a", // 5 – 10 min orange
  severe: "#d92121",   // > 10 min  red
} as const;

// Early arrival (<=0) and on-time treated as `ok` (green); positive minutes ramp up.
// GTFS-RT dep_delay is signed: negative = early, positive = late.
export function delayColor(minutes: number): string {
  if (minutes <= 0) return DELAY_RAMP.ok;
  if (minutes < 2) return DELAY_RAMP.ok;
  if (minutes < 5) return DELAY_RAMP.mild;
  if (minutes < 10) return DELAY_RAMP.moderate;
  return DELAY_RAMP.severe;
}

// Density ramp for the overview heatmap (heatmap-density 0..1 -> color). Runs
// violet -> magenta -> orange -> red, deliberately avoiding green/tan/blue so the
// LOW end stays visible over OSM landcover, GSI 淡色, and dark imagery alike — the
// per-dot warm DELAY_RAMP washed out at the low end on those bases. Red is reserved
// for the genuinely hot, dense core, not painted across the whole field.
export const HEAT_RAMP: ReadonlyArray<readonly [number, string]> = [
  [0, "rgba(124,58,237,0)"],
  [0.12, "rgba(124,58,237,0.5)"],
  [0.35, "#9333ea"],
  [0.58, "#c0359a"],
  [0.78, "#e0633a"],
  [0.92, "#e0492a"],
  [1, "#d92121"],
] as const;
