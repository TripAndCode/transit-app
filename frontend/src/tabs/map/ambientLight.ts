export type AmbientLight = { color: string; opacity: number };

/** Hues chosen off the delay ramp (teal → amber → rust): indigo, and a pale
 *  amber that never approaches the severe tiers at these opacities. */
export const LIGHT_DAWN = "#5B6CAD";
export const LIGHT_DUSK = "#D9A066";
export const LIGHT_NIGHT = "#3B4A8C";
export const LIGHT_MAX_OPACITY = { dawn: 0.08, dusk: 0.07, night: 0.1 } as const;
export const LIGHT_OFF: AmbientLight = { color: "#000000", opacity: 0 };

const DAWN_END = 7;
const DUSK_START = 16;
const DUSK_FULL = 18;
const NIGHT_START = 19.5;
const NIGHT_BASE_OPACITY = 0.03;
const NIGHT_OPACITY_PER_HOUR = 0.02;

/** "HH:MM" (a playback frame's `t`) → decimal hour, or null when unparseable. */
export function frameHour(t: string): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(t);
  if (!m) return null;
  return Number(m[1]) + Number(m[2]) / 60;
}

/** A tint for the hour of a playback frame: cool before 07:00, nothing
 *  through the day, amber toward 18:00, indigo after 19:30. Every branch is
 *  capped so the basemap stays a map. */
export function lightFor(hour: number | null, enabled: boolean): AmbientLight {
  if (!enabled || hour == null) return LIGHT_OFF;
  if (hour < DAWN_END) {
    return { color: LIGHT_DAWN, opacity: Math.min(LIGHT_MAX_OPACITY.dawn, (LIGHT_MAX_OPACITY.dawn * (DAWN_END - hour)) / 2) };
  }
  if (hour < DUSK_START) return LIGHT_OFF;
  if (hour < NIGHT_START) {
    return { color: LIGHT_DUSK, opacity: LIGHT_MAX_OPACITY.dusk * Math.min(1, (hour - DUSK_START) / (DUSK_FULL - DUSK_START)) };
  }
  return {
    color: LIGHT_NIGHT,
    opacity: Math.min(LIGHT_MAX_OPACITY.night, NIGHT_BASE_OPACITY + (hour - NIGHT_START) * NIGHT_OPACITY_PER_HOUR),
  };
}
