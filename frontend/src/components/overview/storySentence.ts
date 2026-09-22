import type { TFunction } from "i18next";
import type { OverviewConcentration, OverviewHeadline, OverviewPeakHour } from "../../api/types";

/** Below this magnitude (minutes) the change since last week reads as noise,
 *  not a real trend -- a dead zone around zero so a +/-0.1 min wobble does
 *  not flip the sentence between "behind" and "ahead" on every refresh. */
const FLAT_THRESHOLD_MIN = 0.5;

/** How many of `concentration.top_routes` the sentence's "N routes carry M%
 *  of delay" clause names. Deliberately fewer than ConcentrationBar's own
 *  card default (5) -- a single sentence needs a tighter number to stay
 *  readable as prose. */
const STORY_TOP_N = 3;

type StoryBranch = "behind" | "ahead" | "flat";

function branchFor(deltaMin: number | null): StoryBranch {
  if (deltaMin == null || Math.abs(deltaMin) < FLAT_THRESHOLD_MIN) return "flat";
  return deltaMin > 0 ? "behind" : "ahead";
}

/**
 * One deterministic sentence for the period-overview hero: how today's
 * average delay compares to last week, which hour is heaviest, and how
 * concentrated delay is among a few routes. Pure template selection plus
 * i18n interpolation -- no LLM, no randomness, same inputs always produce
 * the same key and values.
 *
 * Falls back to the shorter `{branch}_short` template (delta only) whenever
 * `peak` or `concentration` has nothing to say, rather than interpolating
 * `undefined` into a half-built sentence. A missing/null `delta_min` (no
 * baseline to compare against) is treated as "flat" -- there is no known
 * direction to report -- and formats as `0.0` for the short template.
 */
export function storySentence(
  headline: Pick<OverviewHeadline, "delta_min">,
  peak: OverviewPeakHour | null,
  concentration: OverviewConcentration | null,
  t: TFunction,
): string {
  const branch = branchFor(headline.delta_min);
  const delta = headline.delta_min != null ? Math.abs(headline.delta_min).toFixed(1) : "0.0";

  const topRoutes = concentration?.top_routes.slice(0, STORY_TOP_N) ?? [];
  if (peak == null || topRoutes.length === 0) {
    return t(`overview.story.${branch}_short`, { delta });
  }

  const share = Math.round(topRoutes.reduce((sum, r) => sum + r.share_pct, 0));
  // `count` rather than a plain interpolation: one dominant route is a real
  // case (a small agency may only have one observed route at all), and the
  // English clause has to read "the top route", not "the top 1 routes".
  return t(`overview.story.${branch}`, {
    delta,
    peak: peak.peak_hour,
    share,
    count: topRoutes.length,
  });
}
