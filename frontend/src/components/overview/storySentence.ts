import type { TFunction } from "i18next";
import type { OverviewConcentration, OverviewHeadline, OverviewServiceSplitDay } from "../../api/types";
import { CONCENTRATION_TOP_N } from "./concentrationTopN";
import { fixedTenth } from "../../utils/format";

/** A change under this many minutes is about the same: the figures print to
 *  0.1 min, so a smaller change is one a reader cannot see in them. */
export const FLAT_MAX_DELTA_MIN = 0.1;
/** A change under this share (percent) of the window before is about the
 *  same, whatever its minutes: an agency averaging nine minutes moves by more
 *  than 0.1 min from one week to the next without anything having changed. */
export const FLAT_MAX_DELTA_PCT = 5;

type StoryBranch = "behind" | "ahead" | "flat";

/** Inclusive length, in days, of a window from `from` to `to` (ISO dates). */
export function windowDays(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000) + 1;
}

/** "About the same" is claimed only for a change under either bound. With no
 *  share to judge by (a window before at 0 min), minutes alone decide. */
function branchFor(deltaMin: number, deltaPct: number | null): StoryBranch {
  const flat = Math.abs(deltaMin) < FLAT_MAX_DELTA_MIN || (deltaPct != null && Math.abs(deltaPct) < FLAT_MAX_DELTA_PCT);
  if (flat) return "flat";
  return deltaMin > 0 ? "behind" : "ahead";
}

type ComparedHeadline = Pick<
  OverviewHeadline,
  "avg_min" | "baseline_avg_min" | "delta_min" | "delta_pct" | "window_from" | "window_to"
>;

/**
 * The period-overview hero's verdict: a short status chip and one sentence
 * stating the headline window's average, the same-length window's before it,
 * and the change between them. Pure template selection plus i18n
 * interpolation -- no LLM, no randomness, same inputs always produce the same
 * keys and values.
 *
 * The caller gives a verdict only to a headline with a window to compare
 * against; a missing figure prints as 0.0 rather than failing.
 */
export function storyVerdict(headline: ComparedHeadline, t: TFunction): { state: string; sentence: string } {
  const deltaMin = headline.delta_min ?? 0;
  const branch = branchFor(deltaMin, headline.delta_pct);
  const count = windowDays(headline.window_from, headline.window_to);
  const delta = fixedTenth(Math.abs(deltaMin));
  const current = fixedTenth(headline.avg_min ?? 0);
  const previous = fixedTenth(headline.baseline_avg_min ?? 0);
  if (branch === "flat") {
    return {
      state: t("overview.story.state_flat", { count }),
      sentence: t("overview.story.flat", { count, current, previous }),
    };
  }
  const change =
    headline.delta_pct != null
      ? t("overview.story.change_pct", { delta, pct: Math.round(Math.abs(headline.delta_pct)) })
      : t("overview.story.change", { delta });
  return {
    state: t(`overview.story.state_${branch}`, { count, delta }),
    sentence: t(`overview.story.${branch}`, { count, current, previous, change }),
  };
}

/** The latest day is called out only when it is among this many slowest... */
const LATEST_DAY_MAX_RANK = 3;
/** ...of at least this many days observed: over fewer, a rank says little. */
const LATEST_DAY_MIN_DAYS = 7;
/** A latest day observed less than this share of a usual day of its own day
 *  type was collected only in part, so its average covers some of its hours
 *  and is not ranked against whole days. */
const LATEST_DAY_MIN_SHARE = 0.5;
/** The other days of its type a latest day needs for "a usual day" to mean
 *  anything. */
const LATEST_DAY_MIN_PEERS = 3;

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/** Where the latest day (`latestDate`, the headline window's last day) stands
 *  among the days observed in the period, slowest first, when it is one of
 *  the slowest few; null otherwise. Ties share a rank. Only days with data
 *  count, so `days` can be fewer than the period's length. */
export function latestDayStanding(
  daily: OverviewServiceSplitDay[],
  latestDate: string,
): { date: string; avgMin: number; rank: number; days: number } | null {
  const latest = daily.find((d) => d.date === latestDate);
  if (!latest || daily.length < LATEST_DAY_MIN_DAYS) return null;
  const peers = daily.filter((d) => d.day_type === latest.day_type && d.date !== latest.date).map((d) => d.samples);
  if (peers.length < LATEST_DAY_MIN_PEERS || latest.samples < median(peers) * LATEST_DAY_MIN_SHARE) return null;
  const rank = 1 + daily.filter((d) => d.avg_min > latest.avg_min).length;
  return rank <= LATEST_DAY_MAX_RANK ? { date: latest.date, avgMin: latest.avg_min, rank, days: daily.length } : null;
}

/** The share of delay (whole percent) carried by the routes the concentration
 *  card lists, and how many routes that is; null when there are none. */
export function topRoutesShare(concentration: OverviewConcentration | null): { share: number; count: number } | null {
  const topRoutes = concentration?.top_routes.slice(0, CONCENTRATION_TOP_N) ?? [];
  if (topRoutes.length === 0) return null;
  return {
    share: Math.round(topRoutes.reduce((sum, r) => sum + r.share_pct, 0)),
    count: topRoutes.length,
  };
}
