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

// Continuous calm ramp for *relative* (within-view) severity: t=0 → sage, t=1 → orange.
// Stops short of the absolute ramp's alarm red on purpose, so a narrow within-view
// spread (e.g. an agency whose delays sit at 1.6–3.3 min) still reads as a legible
// gradient without shouting. Use this when the message is "which window is worse
// *here*"; use `delayColor` when absolute severity is the message (e.g. a chip).
const RELATIVE_STOPS = [DELAY_RAMP.ok, DELAY_RAMP.mild, DELAY_RAMP.moderate] as const;

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function rampColor(t: number): string {
  const clamped = Math.max(0, Math.min(1, t));
  const seg = clamped * (RELATIVE_STOPS.length - 1);
  const i = Math.min(Math.floor(seg), RELATIVE_STOPS.length - 2);
  const f = seg - i;
  const a = hexToRgb(RELATIVE_STOPS[i]);
  const b = hexToRgb(RELATIVE_STOPS[i + 1]);
  const mix = a.map((av, k) => Math.round(av + (b[k] - av) * f));
  return `rgb(${mix[0]}, ${mix[1]}, ${mix[2]})`;
}

/** Color a value by its position within [min, max] on the calm relative ramp.
 * When the range is degenerate (all equal), returns the mid tone. */
export function relativeDelayColor(value: number, min: number, max: number): string {
  if (max <= min) return rampColor(0.5);
  return rampColor((value - min) / (max - min));
}
