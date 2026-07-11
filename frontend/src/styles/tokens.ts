// Severity ramp for delays (minutes -> color). Calm at the low end so the
// usual data isn't visually loud; severe (>5 min) is a true red so the
// genuinely problematic stops pop without ambiguity.

// The severe tier (>5 min) is theme-aware: at #d92121 (the light-mode red)
// contrast against the new dark backgrounds is ~3.6:1 — fails WCAG AA's 4.5:1
// for normal text; the dark value is now the artifact mockup's literal
// #A83A1A, which only reaches ~2.95:1 on dark — a deliberately accepted
// tradeoff for design-parity (see global.css's comment on --delay-severe),
// not an oversight. Both live in the `--delay-severe` CSS custom property
// (light #d92121 / dark #A83A1A, defined in global.css) so the cascade is
// the single source of truth.
//
// TWO surfaces, deliberately split — pick by how the caller renders the color:
//  - DOM/React (renders into an inline `style` prop): use `DELAY_RAMP.severe` /
//    `delayColor()`, which return the LITERAL string "var(--delay-severe)". The
//    browser cascade resolves it to the active theme's color automatically, so
//    these consumers recolor on a theme toggle for free — no re-render, no JS.
//  - MapLibre (builds plain-JS paint expressions that CANNOT consume var()):
//    call `severeColorResolved()`, which returns a real parseable hex. These
//    call sites already subscribe to `useThemeSignal` and rebuild their
//    expressions on toggle (see useHeatmapLayer / useRouteOverlay).
const SEVERE_VAR = "var(--delay-severe)";

// Light-mode severe red — the fallback when the CSS custom property can't be
// resolved (e.g. under jsdom, which doesn't apply global.css's cascade; tests
// see this unless they set `--delay-severe` inline).
// NOTE: must stay in sync with global.css's base `:root { --delay-severe: … }`
// light value — there's no build-time link between the two, so a change to one
// must be mirrored in the other by hand.
const SEVERE_FALLBACK = "#d92121";

/** Resolve `--delay-severe` to a concrete hex for callers that need a real,
 *  parseable color string — MapLibre paint expressions, which can't consume
 *  `var()`. Reads the live CSS cascade so it tracks the active theme; falls
 *  back to the light-mode red when unresolved (SSR / jsdom). DOM consumers
 *  should NOT call this — use `DELAY_RAMP.severe` (the literal var) instead so
 *  the cascade recolors them on toggle without a re-render. */
export function severeColorResolved(): string {
  if (typeof document === "undefined") return SEVERE_FALLBACK;
  const v = getComputedStyle(document.documentElement)
    .getPropertyValue("--delay-severe")
    .trim();
  return v || SEVERE_FALLBACK;
}

// Light-mode accent — the fallback when --accent can't be resolved (SSR /
// jsdom), matching global.css's base :root value.
const ACCENT_FALLBACK = "#5b6cad";

/** Resolve `--accent` to a concrete hex for callers that need a real,
 *  parseable color string — MapLibre paint expressions, which can't consume
 *  `var()`. Mirrors severeColorResolved() for the same reason: the default
 *  (no-scrubbed-delay) route-line color needs to track the active theme's
 *  accent, not stay pinned to the old hardcoded blue-purple. */
export function accentColorResolved(): string {
  if (typeof document === "undefined") return ACCENT_FALLBACK;
  const v = getComputedStyle(document.documentElement)
    .getPropertyValue("--accent")
    .trim();
  return v || ACCENT_FALLBACK;
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
  severe: SEVERE_VAR, // > 5 min red, per-theme via --delay-severe
} as const;

// Early arrival (<=0) and on-time treated as `ok` (green); positive minutes ramp up.
// GTFS-RT dep_delay is signed: negative = early, positive = late.
export function delayColor(minutes: number): string {
  if (minutes <= 0) return DELAY_RAMP.ok;
  if (minutes < 1.5) return DELAY_RAMP.ok;
  if (minutes < 3) return DELAY_RAMP.mild;
  if (minutes < 5) return DELAY_RAMP.moderate;
  return DELAY_RAMP.severe;
}

/** Same ramp as `delayColor()`, but MapLibre-safe: the severe tier resolves
 *  to a real hex via `severeColorResolved()` instead of the literal
 *  `var(--delay-severe)` string MapLibre paint expressions can't parse. Use
 *  this (not `delayColor()`) for any MapLibre paint property. */
export function delayColorResolved(minutes: number): string {
  if (minutes < 5) return delayColor(minutes);
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
    1.5,
    DELAY_RAMP.mild,
    3,
    DELAY_RAMP.moderate,
    5,
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
