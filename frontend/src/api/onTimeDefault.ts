import ON_TIME from "./onTimeDefault.json";

/** The on-time window, in seconds either side of the timetable, as the
 *  server names it: an `early_sec` of null counts every early departure. */
export type OnTimeWindow = { early_sec: number | null; late_sec: number };

/** How the scope's `early` spells an unbounded early side, since leaving it
 *  out names the default's. */
export const EARLY_ANY = "any";
export type ScopeEarly = number | typeof EARLY_ANY | null;

// onTimeDefault.json mirrors the server's DEFAULT_PRESET, ON_TIME_PRESETS
// and EARLY_ANY (pipeline/reports/rankings.py); tests/unit/test_on_time_default.py
// ties the file to the server and onTimeDefault.test.ts ties this module to the file.
const PRESETS: Readonly<Record<string, OnTimeWindow>> = ON_TIME.presets;

/** The presets the server names, each with a reader's name under
 *  `definitionMeta.preset`. */
export const PRESET_NAMES: ReadonlySet<string> = new Set(Object.keys(PRESETS));

/** The window of every figure whose request names none. */
export const DEFAULT_WINDOW: OnTimeWindow = PRESETS[ON_TIME.default_preset];

/** The window the scope's `early` and `late` name: each it leaves out is the
 *  default's, and `early` "any" counts every early departure. */
export function scopeWindow(scope: { early: ScopeEarly; late: number | null }): OnTimeWindow {
  const early = scope.early === EARLY_ANY ? null : (scope.early ?? DEFAULT_WINDOW.early_sec);
  return { early_sec: early, late_sec: scope.late ?? DEFAULT_WINDOW.late_sec };
}

/** Whether a departure `delaySec` seconds late is on time under `window`,
 *  both edges included, as the server counts it. */
export function isOnTime(delaySec: number, window: OnTimeWindow): boolean {
  return delaySec <= window.late_sec && (window.early_sec == null || delaySec >= -window.early_sec);
}

/** The scope's `early` for an early bound of `sec` seconds, or none: left out
 *  when it is the default's. */
export function earlyParam(sec: number | null): ScopeEarly {
  if (sec === DEFAULT_WINDOW.early_sec) return null;
  return sec ?? EARLY_ANY;
}
