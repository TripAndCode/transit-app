import { DELAY_RAMP, type DelayBand } from "../../styles/tokens";

/** Fixture bands include "offline" (a feed with nothing to report), which the
 *  delay ramp itself has no color for; everything else reuses the app's one
 *  four-tier severity ramp instead of inventing a second palette. */
export type FixtureBand = DelayBand | "offline";

const OFFLINE_FILL = "#101830";
const OFFLINE_RING = "0 0 0 2px #8C97B5";
const RING = "0 0 0 2px #101830";

// DELAY_RAMP.severe is `var(--delay-severe)`, which tracks the active theme
// so it reads correctly as *text*. A stage dot or marker is never text: it
// sits on the stage/panel's own fixed-dark background, which (like the fill
// colors above) does not follow the theme either. In the light theme,
// `--delay-severe` resolves to a dark rust tuned for white paper and falls
// below 3:1 against that dark surface. `--p-rust-50` is the primitive the
// dark theme already maps `--delay-severe` to, and it never changes with the
// theme, so pinning the severe fill to it keeps the dot visible in both.
const SEVERE_FILL = "var(--p-rust-50)";

/** The fill a stage dot or marker paints in, for the always-dark observatory
 *  stage and Bus Town panel (never theme-dependent: both stay dark in light
 *  and dark mode alike). */
export function fixtureFill(band: FixtureBand): string {
  if (band === "offline") return OFFLINE_FILL;
  if (band === "severe") return SEVERE_FILL;
  return DELAY_RAMP[band];
}

/** The ring (box-shadow) around a stage dot or marker, same dark-stage-only
 *  scope as `fixtureFill`. */
export function fixtureRing(band: FixtureBand): string {
  return band === "offline" ? OFFLINE_RING : RING;
}
