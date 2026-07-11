import { useTranslation } from "react-i18next";
import { useRoutes, useTodayRouteSummary } from "../api/hooks";
import type { OverviewHeadline } from "../api/types";
import { delayColor } from "../styles/tokens";
import { InsightHint } from "./InsightHint";

type Props = {
  headline: OverviewHeadline;
  delayedCount: number;
  agencyId: number;
};

// Mirrors DataStalenessBanner.tsx's relativeAgeHours() exactly, including
// hoisting the Date.now() read into a top-level helper (an inline IIFE in
// the component body trips react-hooks/purity's "impure function during
// render" check; a named top-level function like this — the same shape
// the sibling component already uses — does not).
function relativeAgeHours(iso: string): number {
  const captured = new Date(iso).getTime();
  if (!Number.isFinite(captured)) return NaN;
  return (Date.now() - captured) / (1000 * 60 * 60);
}

export function OverviewHeroRow({ headline, delayedCount, agencyId }: Props) {
  const { t } = useTranslation();
  const { data: routes } = useRoutes(agencyId);
  const totalRoutes = (routes ?? []).filter((r) => r.route_code != null).length;
  const { data: feedSummary } = useTodayRouteSummary(agencyId, { autoRefresh: false });

  const hasBaseline = headline.baseline_avg_min != null && headline.delta_min != null;
  // Always render an explicit sign — the displayed magnitude is
  // Math.abs(delta_min), so without this a delay improvement and a
  // worsening of the same magnitude would render identically.
  const sign = hasBaseline && headline.delta_min! >= 0 ? "+" : "-";

  // Mirrors DataStalenessBanner.tsx's days/hours label branching exactly
  // (that component does not have a sub-1-hour "minutes" case, despite
  // common.rel_minutes_ago existing as a key used elsewhere — matching the
  // specific sibling this tile reuses, not inventing a more granular scheme
  // it doesn't itself use).
  const captured = feedSummary?.latest_captured_at;
  let ageLabel: string | null = null;
  if (captured) {
    const ageH = relativeAgeHours(captured);
    if (Number.isFinite(ageH)) {
      const days = Math.floor(ageH / 24);
      ageLabel =
        days >= 1
          ? t("common.rel_days_ago", { count: days })
          : t("common.rel_hours_ago", { count: Math.floor(ageH) });
    }
  }

  const avgMinColor = headline.avg_min != null ? delayColor(headline.avg_min) : undefined;

  return (
    <div className="ov-kpi-row">
      <div className="ov-kpi-tile">
        <div className="ov-kpi-label">{t("overview.hero_row.avg_delay_label")}</div>
        <div className="ov-kpi-value" style={{ color: avgMinColor }}>
          {headline.avg_min != null ? headline.avg_min.toFixed(1) : "—"}
        </div>
        <div className="ov-kpi-context">
          {hasBaseline
            ? t("overview.hero_row.avg_delay_compared", { sign, min: Math.abs(headline.delta_min!).toFixed(1) })
            : t("overview.hero_row.avg_delay_no_baseline")}
          <InsightHint
            title={t("overview.hero_row.baseline_hint_title")}
            body={t("overview.hero_row.baseline_hint_body")}
          />
        </div>
      </div>
      <div className="ov-kpi-tile">
        <div className="ov-kpi-label">{t("overview.hero_row.delayed_count_label")}</div>
        <div className="ov-kpi-value" style={{ color: avgMinColor }}>
          {t("overview.hero_row.delayed_count_value", { count: delayedCount, total: totalRoutes })}
        </div>
      </div>
      <div className="ov-kpi-tile">
        <div className="ov-kpi-label">{t("overview.hero_row.feed_status_label")}</div>
        <div className="ov-kpi-value ov-kpi-value-small">{t("overview.hero_row.feed_status_ok")}</div>
        {ageLabel && (
          <div className="ov-kpi-context">{t("overview.hero_row.feed_status_updated", { when: ageLabel })}</div>
        )}
      </div>
    </div>
  );
}
