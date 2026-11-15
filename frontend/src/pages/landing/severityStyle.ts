import { DELAY_RAMP, type DelayBand } from "../../styles/tokens";

/** Fixture bands include "offline" (a feed with nothing to report), which the
 *  delay ramp itself has no color for; everything else reuses the app's one
 *  four-tier severity ramp instead of inventing a second palette. */
export type FixtureBand = DelayBand | "offline";

const OFFLINE_FILL = "#101830";
const OFFLINE_RING = "0 0 0 2px #8C97B5";
const RING = "0 0 0 2px #101830";

/** The fill a stage dot or marker paints in, for the always-dark observatory
 *  stage and Bus Town panel (never theme-dependent: both stay dark in light
 *  and dark mode alike). */
export function fixtureFill(band: FixtureBand): string {
  return band === "offline" ? OFFLINE_FILL : DELAY_RAMP[band];
}

/** The ring (box-shadow) around a stage dot or marker, same dark-stage-only
 *  scope as `fixtureFill`. */
export function fixtureRing(band: FixtureBand): string {
  return band === "offline" ? OFFLINE_RING : RING;
}
