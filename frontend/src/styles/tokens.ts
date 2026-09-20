// Severity ramp for delays (minutes -> color). Calm at the low end so the
// usual data isn't visually loud; the severe tier (>=5 min) is the deep end of
// the warm ramp -- loud enough that a genuinely problematic stop is
// unambiguous, deliberately short of an alarm red.

// The severe tier is theme-aware, and the split is load-bearing rather than
// cosmetic: the invariant is that each theme's value clears WCAG AA (4.5:1) as
// text on that theme's own --bg-surface, and no single hex satisfies both --
// dark enough to read on white is too dark to read on a near-black page, and
// the reverse. Both values live in the `--delay-severe` CSS custom property
// (defined per theme in global.css) so the cascade is the single source of
// truth; tokens.test.ts asserts both directions of the invariant, including
// that the cross-theme pairs still fail.
//
// TWO surfaces, deliberately split -- pick by how the caller renders the color:
//  - DOM/React (renders into an inline `style` prop): use `DELAY_RAMP.severe` /
//    `delayColor()`, which return the LITERAL string "var(--delay-severe)". The
//    browser cascade resolves it to the active theme's color automatically, so
//    these consumers recolor on a theme toggle for free -- no re-render, no JS.
//  - MapLibre (builds plain-JS paint expressions that CANNOT consume var()):
//    call `severeColorResolved()`, which returns a real parseable hex. These
//    call sites already subscribe to `useThemeSignal` and rebuild their
//    expressions on toggle (see useOperationsMapLayers).
const SEVERE_VAR = "var(--delay-severe)";

// Light-mode values -- the fallback when the CSS custom property can't be
// resolved (e.g. under jsdom, which doesn't apply global.css's cascade; tests
// see these unless they set the property inline).
// NOTE: each must stay in sync with global.css's base `:root` declaration --
// there's no build-time link between the two, so a change to one must be
// mirrored in the other by hand. tokens.test.ts holds them to it.
const SEVERE_FALLBACK = "#A8391F";
const SURFACE_FALLBACK = "#ffffff";
const ACCENT_FALLBACK = "#187b80";

/** Resolve a CSS custom property to a concrete color for callers that need a
 *  real, parseable string -- MapLibre paint expressions, which can't consume
 *  `var()`. Reads the live cascade so it tracks the active theme; falls back
 *  to the light-mode value when unresolved (SSR / jsdom). */
function cssColor(prop: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(prop).trim();
  return v || fallback;
}

/** The active theme's `--delay-severe` as a concrete hex. DOM consumers should
 *  NOT call this -- use `DELAY_RAMP.severe` (the literal var) instead so the
 *  cascade recolors them on toggle without a re-render. */
export function severeColorResolved(): string {
  return cssColor("--delay-severe", SEVERE_FALLBACK);
}

/** The active theme's `--bg-surface` as a concrete hex. Map marks ring
 *  themselves in it so they separate from the basemap in either theme without
 *  a heavy dark casing. */
export function surfaceColorResolved(): string {
  return cssColor("--bg-surface", SURFACE_FALLBACK);
}

/** The active theme's `--accent` as a concrete hex, for map layers that carry
 *  the product accent (the selected trip's reported trail). */
export function accentColorResolved(): string {
  return cssColor("--accent", ACCENT_FALLBACK);
}

/** Relative luminance per WCAG 2.x, for a `#rrggbb` string. */
function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  const channels = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

/** WCAG contrast ratio between two `#rrggbb` colors, 1:1 to 21:1. Symmetric.
 *  Lives here rather than in a test helper because `readableInkOn` needs it at
 *  runtime, and a second copy would be free to drift from this one. */
export function contrastRatio(a: string, b: string): number {
  const [la, lb] = [luminance(a), luminance(b)];
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

// The two inks a mark's own label can use. Neither is pure black/near-white by
// accident: they are the darkest and lightest surfaces the product already
// paints, so a label sitting on a colored mark still belongs to the palette.
const INK_DARK = "#0F1119";
const INK_LIGHT = "#ffffff";

/** The ink that reads best on `bg` -- for text painted directly onto a
 *  data-colored mark, where the background is a ramp color rather than a
 *  theme surface and so cannot be assumed light or dark. */
export function readableInkOn(bg: string): string {
  return contrastRatio(INK_DARK, bg) >= contrastRatio(INK_LIGHT, bg) ? INK_DARK : INK_LIGHT;
}

const BASE_RAMP = {
  ok: "#2EA87A",       // < 1.5 min
  mild: "#C99A2E",     // 1.5 – 3 min
  moderate: "#D4622A", // 3 – 5 min
} as const;

export const DELAY_RAMP = {
  ...BASE_RAMP,
  // Literal CSS var string for DOM/React consumers — see the block comment
  // above. MapLibre call sites use severeColorResolved() instead.
  severe: SEVERE_VAR, // >= 5 min, per-theme via --delay-severe
} as const;

type DelayBand = "ok" | "mild" | "moderate" | "severe";

// The band-boundary minute values `delayBand()` applies below. Exported so any
// UI that displays the cutoffs as text (e.g. a heatmap legend) can read them
// straight from here instead of re-typing the numbers — a retuned threshold
// then can't silently desync a label from the classifier that decides the color.
export const DELAY_THRESHOLDS = {
  mild: 1.5,
  moderate: 3,
  severe: 5,
} as const;

// Early arrival (<=0) and on-time treated as `ok` (green); positive minutes ramp up.
// GTFS-RT dep_delay is signed: negative = early, positive = late.
// Single source of truth for the delay/severity cutoffs — delayColor() and
// any other consumer that needs the band (not just its color) both go
// through this, so a future retune can't silently desync one from the other.
export function delayBand(minutes: number): DelayBand {
  if (minutes <= 0) return "ok";
  if (minutes < DELAY_THRESHOLDS.mild) return "ok";
  if (minutes < DELAY_THRESHOLDS.moderate) return "mild";
  if (minutes < DELAY_THRESHOLDS.severe) return "moderate";
  return "severe";
}

export function delayColor(minutes: number): string {
  return DELAY_RAMP[delayBand(minutes)];
}

/** Same ramp as `delayColor()`, but MapLibre-safe: the severe tier resolves
 *  to a real hex via `severeColorResolved()` instead of the literal
 *  `var(--delay-severe)` string MapLibre paint expressions can't parse. Use
 *  this (not `delayColor()`) for any MapLibre paint property. */
export function delayColorResolved(minutes: number): string {
  if (minutes < DELAY_THRESHOLDS.severe) return delayColor(minutes);
  return severeColorResolved();
}

/** The delay-ramp color/threshold pairs a MapLibre `step` expression needs after
 *  its `["step", <input>]` prefix: `[ok, 1.5, mild, 3, moderate, 5, severe]`.
 *  Single source of truth for the paint-expression stops shared by the heatmap
 *  and route-overlay layers — spread it (`["step", input, ...severityStepColors()]`)
 *  rather than hand-assembling the array, so a new call site can't accidentally
 *  reach for `DELAY_RAMP.severe` (the literal `var()`, which MapLibre can't parse)
 *  instead of the resolved hex. The severe stop is `severeColorResolved()`, a real
 *  parseable color for exactly that reason. */
export function severityStepColors(): readonly [
  string, number, string, number, string, number, string,
] {
  return [
    DELAY_RAMP.ok,
    DELAY_THRESHOLDS.mild,
    DELAY_RAMP.mild,
    DELAY_THRESHOLDS.moderate,
    DELAY_RAMP.moderate,
    DELAY_THRESHOLDS.severe,
    severeColorResolved(),
  ];
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
