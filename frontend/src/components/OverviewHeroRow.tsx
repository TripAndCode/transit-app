import { useTranslation } from "react-i18next";
import { useRoutes, useTodayRouteSummary } from "../api/hooks";
import type { OverviewConcentration, OverviewHeadline, OverviewPeakHour } from "../api/types";
import { delayColor } from "../styles/tokens";
import { useCountUp } from "../hooks/useCountUp";
import { InsightHint } from "./InsightHint";
import { InlineSparkline } from "./InlineSparkline";
import { storySentence } from "./overview/storySentence";
import { STALE_THRESHOLD_HOURS } from "./DataStalenessBanner";

type Props = {
  headline: OverviewHeadline;
  delayedCount: number;
  agencyId: number;
  sparklinePoints: number[];
  peakHour: OverviewPeakHour | null;
  concentration: OverviewConcentration;
};

// Same shape as DataStalenessBanner.tsx's relativeAgeHours() — including
// hoisting the Date.now() read into a top-level helper (an inline IIFE in
// the component body trips react-hooks/purity's "impure function during
// render" check) — but deliberately NOT identical: this one returns NaN on
// an invalid timestamp (guarded below by Number.isFinite, so a bad
// timestamp reads as "no age" rather than being mistaken for "0h ago =
// fresh"), where the banner returns 0. Don't dedup these into one shared
// helper without preserving that difference.
function relativeAgeHours(iso: string): number {
  const captured = new Date(iso).getTime();
  if (!Number.isFinite(captured)) return NaN;
  return (Date.now() - captured) / (1000 * 60 * 60);
}

export function OverviewHeroRow({
  headline,
  delayedCount,
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

  // Mirrors DataStalenessBanner.tsx's days/hours label branching exactly
  // (that component does not have a sub-1-hour "minutes" case, despite
  // common.rel_minutes_ago existing as a key used elsewhere — matching the
  // specific sibling this tile reuses, not inventing a more granular scheme
  // it doesn't itself use).
  const captured = feedSummary?.latest_captured_at;
  let ageLabel: string | null = null;
  let feedIsStale = false;
  if (captured) {
    const ageH = relativeAgeHours(captured);
    if (Number.isFinite(ageH)) {
      feedIsStale = ageH >= STALE_THRESHOLD_HOURS;
      const days = Math.floor(ageH / 24);
      ageLabel =
        days >= 1
          ? t("common.rel_days_ago", { count: days })
          : t("common.rel_hours_ago", { count: Math.floor(ageH) });
    }
  }

  const avgMinColor = headline.avg_min != null ? delayColor(headline.avg_min) : undefined;
  // Called unconditionally (hooks can't branch on headline.avg_min's
  // nullability) -- the "—" fallback below still renders in place of it when
  // there is nothing to display. Never animates on first mount, only when
  // avg_min changes afterward (an agency switch, a live refresh).
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
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
        />
        <div className="ov-hero-label">{t("overview.hero_row.avg_delay_label")}</div>
        <div className="ov-kpi-value" style={{ color: avgMinColor }}>
          {headline.avg_min != null ? avgMinDisplay.toFixed(1) : "—"}
          <span className="ov-hero-unit">{t("overview.hero_unit_min")}</span>
        </div>
      </div>
      <div>
        {/* A div, not a <p>: InsightHint's root is a div, and a div is not
            valid phrasing content inside a <p>. */}
        <div className="ov-hero-story">
          {story}
          <InsightHint
            title={t("overview.hero_row.baseline_hint_title")}
            body={t("overview.hero_row.baseline_hint_body")}
          />
        </div>
        <div className="ov-hero-sub">
          <div className="ov-hero-sub-item">
            <span className="ov-hero-sub-value">
              {t("overview.hero_row.delayed_count_value", { count: delayedCountDisplay, total: totalRoutes })}
            </span>
            {t("overview.hero_row.delayed_count_label")}
          </div>
          <div className="ov-hero-sub-item">
            <span className="ov-hero-sub-value">
              {t(feedIsStale ? "overview.hero_row.feed_status_stale" : "overview.hero_row.feed_status_ok")}
            </span>
            {ageLabel ? t("overview.hero_row.feed_status_updated", { when: ageLabel }) : t("overview.hero_row.feed_status_label")}
          </div>
        </div>
      </div>
    </div>
  );
}
