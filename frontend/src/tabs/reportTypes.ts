import type { TFunction } from "i18next";

/**
 * The 12 analysis/report type ids, in the order AnalysisTab's report list
 * groups them. Its own home (not inside AnalysisTab.tsx, a lazy-loaded
 * route) so other, eagerly-mounted UI — the command palette — can reuse the
 * id set without statically pulling AnalysisTab's chart dependencies into
 * the entry bundle.
 */
export const REPORT_TYPE_IDS = [
  "ranking",
  "ranking_best",
  "on_time",
  "worst_5min",
  "trend",
  "compare_ranking",
  "dow_weekday",
  "dow_weekend",
  "dwell_run",
  "route_forecast",
  "council_summary",
  "delay_certificate",
] as const;

type ReportTypeId = (typeof REPORT_TYPE_IDS)[number];

/** id → translated label, mirroring buildTimeBandOptions' shared-builder
 *  pattern so this mapping has exactly one place it's derived. */
export function buildReportTypeLabels(t: TFunction): Record<ReportTypeId, string> {
  return {
    ranking: t("reports.type.ranking"),
    ranking_best: t("reports.type.ranking_best"),
    on_time: t("reports.type.on_time"),
    worst_5min: t("reports.type.worst_5min"),
    trend: t("reports.type.trend"),
    compare_ranking: t("reports.type.compare_ranking"),
    dow_weekday: t("reports.type.dow_weekday"),
    dow_weekend: t("reports.type.dow_weekend"),
    dwell_run: t("reports.type.dwell_run"),
    route_forecast: t("reports.type.route_forecast"),
    council_summary: t("reports.type.council_summary"),
    delay_certificate: t("reports.type.delay_certificate"),
  };
}
