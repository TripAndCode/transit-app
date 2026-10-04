import { useTranslation } from "react-i18next";
import { useRoutes, useTodayRouteSummary } from "../api/hooks";
import type { TFunction } from "i18next";
import type { OverviewConcentration, OverviewHeadline, OverviewPeakHour, RouteSummaryResponse } from "../api/types";
import { useCountUp } from "../hooks/useCountUp";
import { InsightHint } from "./InsightHint";
import { InlineSparkline } from "./InlineSparkline";
import { periodMean } from "./periodMean";
import { storySentence } from "./overview/storySentence";
import { MAX_REPORT_AGE_MS } from "../tabs/map/liveRowsFilter";
import { quietFor } from "../tabs/map/format";
import { EM_DASH, formatDateRange, formatReportTime } from "../utils/format";

type Props = {
  headline: OverviewHeadline;
  delayedCount: number;
  /** The average delay at or above which a route counts as delayed. */
  delayedThresholdMin: number;
  agencyId: number;
  sparklinePoints: number[];
  peakHour: OverviewPeakHour | null;
  concentration: OverviewConcentration;
};

// The Date.now() read lives in a top-level helper because an inline read in
// the component body trips react-hooks/purity's "impure function during
// render" check. NaN for an unparseable timestamp, so it reads as "no age"
// rather than as a fresh report.
function reportAgeMs(iso: string): number {
  const captured = Date.parse(iso);
  return Number.isFinite(captured) ? Date.now() - captured : NaN;
}

/** The feed-status line, by the same rule as Live's freshness badge: a feed
 *  whose newest report is older than the live window is quiet, however recent
 *  the aggregates are. Nothing is claimed while the status is unread or its
 *  timestamp unreadable. */
function describeFeedStatus(summary: RouteSummaryResponse | undefined, t: TFunction): string {
  if (!summary) return EM_DASH;
  if (!summary.latest_captured_at) return t("overview.hero_row.feed_status_none");
  const ageMs = reportAgeMs(summary.latest_captured_at);
  if (!Number.isFinite(ageMs)) return EM_DASH;
  return ageMs > MAX_REPORT_AGE_MS
    ? t("overview.hero_row.feed_status_quiet", { duration: quietFor(ageMs, t) })
    : t("overview.hero_row.feed_status_live");
}

export function OverviewHeroRow({
  headline,
  delayedCount,
  delayedThresholdMin,
  agencyId,
  sparklinePoints,
  peakHour,
  concentration,
}: Props) {
  const { t } = useTranslation();
  const { data: routes } = useRoutes(agencyId);
  const totalRoutes = (routes ?? []).filter((r) => r.route_code != null).length;
  const { data: feedSummary } = useTodayRouteSummary(agencyId, { autoRefresh: false });

  const hasBaseline = headline.baseline_avg_min != null && headline.delta_min != null;

  const feedStatus = describeFeedStatus(feedSummary, t);
  const captured = feedSummary?.latest_captured_at;
  const lastReport = captured && Number.isFinite(Date.parse(captured)) ? formatReportTime(captured) : null;
  const range = formatDateRange(headline.window_from, headline.window_to, { year: false });

  // The sparkline scales to its own min..max, so it is anchored on the period
  // mean rather than 0: the absolute figure is already shown beside it, and a
  // 0 axis would flatten the day-to-day variation the sparkline exists to show.
  const sparklineMean = periodMean(sparklinePoints);

  // Called unconditionally (hooks can't branch on headline.avg_min's
  // nullability) -- the "—" fallback below still renders in place of it when
  // there is nothing to display.
  const avgMinDisplay = useCountUp(headline.avg_min ?? 0, { decimals: 1 });
  const delayedCountDisplay = useCountUp(delayedCount, { decimals: 0 });

  // storySentence needs a real delta to pick a direction -- with no
  // baseline at all there's nothing to compare against, so this keeps the
  // plain "no comparison data" text rather than feeding it a fabricated
  // delta_min: null and letting the sentence's own "flat" fallback claim
  // the period is "about the same" (a specific claim this case cannot back).
  const story = hasBaseline
    ? storySentence(headline, peakHour, concentration, t)
    : t("overview.hero_row.avg_delay_no_baseline");

  return (
    <div className="ov-hero">
      <div className="ov-hero-figure">
        <InlineSparkline
          points={sparklinePoints}
          width={400}
          height={190}
          preserveAspectRatio="none"
          showLabels={false}
          showEndDot={false}
          baseline={sparklineMean ?? undefined}
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
        />
        <div className="ov-hero-label">
          {t("overview.hero_row.avg_delay_label", { range })}
        </div>
        <div className="ov-kpi-value">
          {headline.avg_min != null ? avgMinDisplay.toFixed(1) : "—"}
          <span className="ov-hero-unit">{t("overview.hero_unit_min")}</span>
        </div>
        {sparklineMean != null && (
          <div className="ov-hero-baseline">
            {t("overview.hero_row.sparkline_baseline", { value: sparklineMean.toFixed(1) })}
          </div>
        )}
      </div>
      <div>
        {/* A div, not a <p>: InsightHint's root is a div, and a div is not
            valid phrasing content inside a <p>. */}
        <div className="ov-hero-story">
          {story}
          <InsightHint
            title={t("overview.hero_row.baseline_hint_title")}
            body={t("overview.hero_row.baseline_hint_body", { range })}
          />
        </div>
        <div className="ov-hero-sub">
          <div className="ov-hero-sub-item">
            <span className="ov-hero-sub-value">
              {t("overview.hero_row.delayed_count_value", { count: delayedCountDisplay, total: totalRoutes })}
            </span>
            {t("overview.hero_row.delayed_count_label", { min: delayedThresholdMin })}
          </div>
          <div className="ov-hero-sub-item">
            <span className="ov-hero-sub-value">{feedStatus}</span>
            {lastReport && t("overview.hero_row.feed_status_last", { time: lastReport })}
          </div>
        </div>
      </div>
    </div>
  );
}
