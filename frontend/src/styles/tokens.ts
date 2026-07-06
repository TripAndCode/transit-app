// Severity ramp for delays (minutes -> color). Calm at the low end so the
// usual data isn't visually loud; severe (>10 min) is a true red so the
// genuinely problematic stops pop without ambiguity.

// Light-mode severe red, and the fallback when the CSS custom property can't be
// resolved (e.g. under jsdom, which doesn't apply global.css's cascade — tests
// see this unless they set `--delay-severe` inline).
const SEVERE_FALLBACK = "#d92121";

// `severe` is a live getter reading the `--delay-severe` CSS custom property
// (light #d92121 / dark #F04438, defined in global.css). At #d92121 (the
// light-mode red) contrast against the new dark backgrounds is ~3.6:1 — fails
// WCAG AA's 4.5:1 for normal text; #F04438 fixes that on dark (~5:1) but itself
// fails on the light background (~3.8:1), so the two themes need genuinely
// different values. Sourcing that value from CSS (rather than re-deriving it
// from data-theme in JS) makes the CSS cascade the single source of truth: DOM
// consumers recolor via `var(--delay-severe)` for free on a theme toggle, and
// only the imperative MapLibre call sites — which can't consume `var()` — need
// an explicit theme dependency to rebuild their style expressions. Reading the
// custom property here keeps every existing consumer of DELAY_RAMP.severe
// unchanged (no theme param threaded through delayColor/relativeDelayColor).
function severeColor(): string {
  if (typeof document === "undefined") return SEVERE_FALLBACK;
  const v = getComputedStyle(document.documentElement)
    .getPropertyValue("--delay-severe")
    .trim();
  return v || SEVERE_FALLBACK;
}

export const DELAY_RAMP = {
  ok: "#8fb88f",       // < 2 min   sage
  mild: "#d4b878",     // 2 – 5 min sand
  moderate: "#e07a3a", // 5 – 10 min orange
  get severe(): string {
    return severeColor(); // > 10 min red, per-theme via --delay-severe
  },
};

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
