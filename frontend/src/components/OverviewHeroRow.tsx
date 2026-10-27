import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useTodayRouteSummary } from "../api/hooks";
import type { TFunction } from "i18next";
import type {
  OverviewConcentration,
  OverviewHeadline,
  OverviewPeakHour,
  OverviewServiceSplitDay,
  RouteSummaryResponse,
} from "../api/types";
import { useCountUp } from "../hooks/useCountUp";
import { InsightHint } from "./InsightHint";
import { InlineSparkline } from "./InlineSparkline";
import { latestDayStanding, storyVerdict, topRoutesShare, windowDays } from "./overview/storySentence";
import { DayPulseRibbon } from "./overview/DayPulseRibbon";
import { BREATH_WINDOW_MS, isBreathing } from "./overview/freshness";
import { MAX_REPORT_AGE_MS } from "../tabs/map/liveRowsFilter";
import { quietFor } from "../tabs/map/format";
import { EM_DASH, fixedTenth, formatDate, formatHourRange, formatReportTime } from "../utils/format";

/** A hero fact row, named for the card that explains it. */
export type HeroFact = "peak_hour" | "concentration" | "late_routes";

type Props = {
  headline: OverviewHeadline;
  /** Routes over the routes-to-check list's top-band bound, over every
   *  route: the figure that list's top-band header prints. */
  severeCount: number;
  /** That bound, in minutes. */
  severeThresholdMin: number;
  /** The routes that count is out of: those observed at least `minSamples`
   *  times in the headline's window. */
  routeCount: number;
  minSamples: number;
  agencyId: number;
  sparklinePoints: number[];
  /** The window's sample-weighted mean, from the API: the sparkline's
   *  reference line. */
  periodAvgMin: number | null;
  /** The headline's window as text: the headline and the late-route count. */
  headlineRange: string;
  /** The period the API counted, as text: the heaviest hour, the delay share
   *  and the sparkline's reference line. */
  periodRange: string;
  peakHour: OverviewPeakHour | null;
  concentration: OverviewConcentration;
  /** The period's days with data, oldest first: where the headline's last
   *  day stands among them. */
  daily: OverviewServiceSplitDay[];
  /** Opens the card behind a fact row. Each row exists only while that card
   *  has something to show. */
  onOpenFact: (fact: HeroFact) => void;
};

// The Date.now() read lives in a top-level helper because an inline read in
// the component body trips react-hooks/purity's "impure function during
// render" check. NaN for an unparseable timestamp, so it reads as "no age"
// rather than as a fresh report. The compiler caches a call per argument, not
// per clock, so a render reads the age once per report: closing a window
// needs a timer and state, as the breath and live windows below have. (A
// report further ahead than the skew allowance never breathes; nothing times
// its way into the window.)
function reportAgeMs(iso: string): number {
  const captured = Date.parse(iso);
  return Number.isFinite(captured) ? Date.now() - captured : NaN;
}

// Hoisted for the same react-hooks/purity reason as reportAgeMs.
function breathingNow(iso: string): boolean {
  return isBreathing(iso, Date.now());
}

type FeedState = { kind: "unknown" } | { kind: "none" } | { kind: "live" } | { kind: "quiet"; ageMs: number };

/** The feed's state, by the same rule as Live's freshness badge: a feed whose
 *  newest report is older than the live window is quiet, however recent the
 *  aggregates are. Unknown while the status is unread or its timestamp
 *  unreadable, so nothing is claimed then. `liveWindowClosed` is the timer's
 *  word that the window has closed since the age was last read. */
function readFeed(summary: RouteSummaryResponse | undefined, liveWindowClosed: boolean): FeedState {
  if (!summary) return { kind: "unknown" };
  if (!summary.latest_captured_at) return { kind: "none" };
  const ageMs = reportAgeMs(summary.latest_captured_at);
  if (!Number.isFinite(ageMs)) return { kind: "unknown" };
  return liveWindowClosed || ageMs > MAX_REPORT_AGE_MS ? { kind: "quiet", ageMs } : { kind: "live" };
}

function describeFeedStatus(feed: FeedState, t: TFunction): string {
  switch (feed.kind) {
    case "unknown":
      return EM_DASH;
    case "none":
      return t("overview.hero_row.feed_status_none");
    case "quiet":
      return t("overview.hero_row.feed_status_quiet", { duration: quietFor(feed.ageMs, t) });
    case "live":
      return t("overview.hero_row.feed_status_live");
  }
}

export function OverviewHeroRow({
  headline,
  severeCount,
  severeThresholdMin,
  routeCount,
  minSamples,
  agencyId,
  sparklinePoints,
  periodAvgMin,
  headlineRange: range,
  periodRange,
  peakHour,
  concentration,
  daily,
  onOpenFact,
}: Props) {
  const { t } = useTranslation();
  const { data: feedSummary } = useTodayRouteSummary(agencyId, { autoRefresh: false });

  const hasBaseline = headline.baseline_avg_min != null && headline.delta_min != null;

  const captured = feedSummary?.latest_captured_at;
  const lastReport = captured && Number.isFinite(Date.parse(captured)) ? formatReportTime(captured) : null;

  // The dot breathes inside BREATH_WINDOW_MS and is green inside the live
  // window. Nothing refetches this summary on its own, so each window's close
  // is scheduled here: a timer records which report it closed for, and a
  // newer report opens fresh windows.
  const [breathClosedFor, setBreathClosedFor] = useState<string | null>(null);
  const [liveClosedFor, setLiveClosedFor] = useState<string | null>(null);
  const breathing = captured != null && breathClosedFor !== captured && breathingNow(captured);
  const feed = readFeed(feedSummary, captured != null && liveClosedFor === captured);
  const feedStatus = describeFeedStatus(feed, t);
  useEffect(() => {
    if (!captured) return;
    const age = reportAgeMs(captured);
    if (!Number.isFinite(age)) return;
    const timers: number[] = [];
    if (BREATH_WINDOW_MS - age > 0) timers.push(window.setTimeout(() => setBreathClosedFor(captured), BREATH_WINDOW_MS - age));
    // Live holds through MAX_REPORT_AGE_MS itself, so the close lands just past it.
    if (MAX_REPORT_AGE_MS - age >= 0) timers.push(window.setTimeout(() => setLiveClosedFor(captured), MAX_REPORT_AGE_MS - age + 1));
    return () => timers.forEach((id) => window.clearTimeout(id));
  }, [captured]);

  // The sparkline scales to its own min..max, so it is anchored on the period
  // mean rather than 0: the absolute figure is already shown beside it, and a
  // 0 axis would flatten the day-to-day variation the sparkline exists to show.
  // No line under two points, which InlineSparkline refuses to draw: a legend
  // keyed on it would caption a chart that never rendered.
  const sparklineMean = sparklinePoints.length >= 2 ? periodAvgMin : null;

  // Called unconditionally (hooks can't branch on headline.avg_min's
  // nullability) -- the "—" fallback below still renders in place of it when
  // there is nothing to display.
  const avgMinDisplay = useCountUp(headline.avg_min ?? 0, { decimals: 1 });
  const severeCountDisplay = useCountUp(severeCount, { decimals: 0 });

  // A verdict needs a real delta to pick a direction -- with no baseline at
  // all there's nothing to compare against, so this keeps the plain "no
  // comparison data" text and no chip rather than letting the verdict's own
  // "flat" fallback claim the period is "about the same" (a specific claim
  // this case cannot back).
  const verdict = hasBaseline ? storyVerdict(headline, t) : null;
  const latestDay = latestDayStanding(daily, headline.window_to);
  const share = topRoutesShare(concentration);

  return (
    <>
      <div className="ov-hero-verdict">
        {verdict && <span className="ov-hero-state">{verdict.state}</span>}
        <p className="ov-hero-story">{verdict ? verdict.sentence : t("overview.hero_row.avg_delay_no_baseline")}</p>
        <InsightHint
          title={t("overview.hero_row.baseline_hint_title")}
          body={t("overview.hero_row.baseline_hint_body", { range })}
        />
        {latestDay && (
          <p className="ov-hero-note">
            {t(`overview.story.latest_day_${latestDay.rank}`, {
              date: formatDate(latestDay.date, { year: false, weekday: true }),
              avg: fixedTenth(latestDay.avgMin),
              days: latestDay.days,
              range: periodRange,
            })}
          </p>
        )}
      </div>
      <div className="ov-hero">
        {/* Both charts live in the figure column, which clips them: the fact
            list beside it stays on a plain surface. */}
        <div className="ov-hero-figure">
          <InlineSparkline
            points={sparklinePoints}
            width={400}
            height={190}
            preserveAspectRatio="none"
            showEndDot={false}
            baseline={sparklineMean ?? undefined}
            style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
          />
          {peakHour && <DayPulseRibbon byHour={peakHour.by_hour} />}
          <div className="ov-hero-label">
            {t("overview.hero_row.avg_delay_label", { count: windowDays(headline.window_from, headline.window_to), range })}
          </div>
          <div className="ov-kpi-value num">
            {headline.avg_min != null ? fixedTenth(avgMinDisplay) : "—"}
            <span className="ov-hero-unit">{t("overview.hero_unit_min")}</span>
          </div>
          {sparklineMean != null && (
            <div className="ov-hero-baseline">
              {t("overview.hero_row.sparkline_baseline", { range: periodRange, value: fixedTenth(sparklineMean) })}
            </div>
          )}
          <div className="ov-hero-feed">
            {/* Green only for a live feed; a quiet or never-reporting one
                keeps a neutral dot, and an unread status shows none. */}
            {feed.kind !== "unknown" && (
              <i
                className={`ov-fresh-dot${breathing ? " ov-fresh-dot--live" : ""}${feed.kind === "live" ? "" : " ov-fresh-dot--stale"}`}
                aria-hidden="true"
              />
            )}
            <span className="ov-hero-feed-status">{feedStatus}</span>
            {lastReport && <span>{t("overview.hero_row.feed_status_last", { time: lastReport })}</span>}
          </div>
        </div>
        <div className="ov-hero-text">
          <ul className="ov-hero-facts">
            {peakHour && (
              <FactRow
                label={t("overview.hero_row.fact_peak_hour")}
                period={periodRange}
                onClick={() => onOpenFact("peak_hour")}
              >
                {formatHourRange(peakHour.peak_hour)}
              </FactRow>
            )}
            {share && (
              <FactRow
                label={t("overview.hero_row.fact_concentration", { count: share.count })}
                period={periodRange}
                onClick={() => onOpenFact("concentration")}
              >
                {share.share}
                <span className="ov-hero-fact-unit">{t("overview.hero_row.fact_share_unit")}</span>
              </FactRow>
            )}
            {/* The hero's only warm value, and only while a route is over the
                bound: it is the finding that asks for action. Gated on the
                real count, not the counting-up display, which starts from 0. */}
            {/* Out of the routes the list ranks, not the timetable's: the
                count and its total are one population, and the period line
                says which. */}
            <FactRow
              label={t("overview.hero_row.fact_late_routes", { min: severeThresholdMin })}
              period={t("overview.hero_row.fact_late_routes_period", { range, min: minSamples })}
              onClick={() => onOpenFact("late_routes")}
              attention={severeCount > 0}
            >
              {severeCountDisplay}
              <span className="ov-hero-fact-unit">
                {t("overview.hero_row.fact_late_routes_of", { count: routeCount })}
              </span>
            </FactRow>
          </ul>
        </div>
      </div>
    </>
  );
}

/** One fact, dated: the facts count different windows, so each says which. */
function FactRow({
  label,
  period,
  onClick,
  attention = false,
  children,
}: {
  label: string;
  period: string;
  onClick: () => void;
  attention?: boolean;
  children: ReactNode;
}) {
  return (
    <li>
      <button type="button" className={`ov-hero-fact${attention ? " ov-hero-fact--attn" : ""}`} onClick={onClick}>
        <span className="ov-hero-fact-label">
          {label}{" "}
          <span className="ov-hero-fact-period">{period}</span>
        </span>
        <span className="ov-hero-fact-value num">{children}</span>
        <span className="ov-hero-fact-go" aria-hidden="true">›</span>
      </button>
    </li>
  );
}
