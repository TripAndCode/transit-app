/**
 * Per-route "minimum performance standard" bonus/malus simulation panel,
 * comparing each configured threshold against the Excess Waiting Time or
 * vehicle-km-delivered rate. Renders nothing when this agency has zero
 * configured `route_performance_standards` rows (see
 * pipeline/reports/performance_standard.py) -- there is no ingestion
 * pipeline or admin UI for this table, so an agency with no rows simply has
 * no standards configured yet, same convention as ridership_weights.
 *
 * This panel is an INTERNAL SIMULATION/ESTIMATE ONLY, never an actual
 * invoice or contractual output -- both a prominent client-side badge
 * (i18n key `reports.performance_standard.simulation_badge`, always
 * rendered) and the server's own already-localized disclaimer text (see
 * `PerformanceStandardsResponse.disclaimer`) must stay attached to these
 * figures.
 */
import { useTranslation } from "react-i18next";
import { usePerformanceStandards } from "../api/hooks";
import { useRouteNames } from "../api/useRouteNames";
import { RouteLabel } from "./RouteLabel";
import type { Scope } from "../api/scope";
import { SHARED_TABLE, th, td } from "./tableStyles";
import type { PerformanceStandardRow } from "../api/types";
import { Skeleton } from "./Skeleton";
import { ErrorBanner } from "./ErrorBanner";
import { formatDuration } from "../utils/format";

function fmtMetricValue(v: number | null, metricType: PerformanceStandardRow["metric_type"]): string {
  if (v == null) return "—";
  return metricType === "ewt_sec" ? formatDuration(v) : `${v.toFixed(1)}%`;
}

function fmtAchievementRate(v: number | null): string {
  return v == null ? "—" : `${(v * 100).toFixed(1)}%`;
}

function fmtEstimate(v: number | null): string {
  if (v == null) return "—";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${sign}${Math.abs(v).toFixed(1)}`;
}

export function PerformanceStandardPanel({ aid, ctx }: { aid: number; ctx: Scope }) {
  const { t } = useTranslation();
  const routeNames = useRouteNames(aid);
  const { data, isLoading, error, refetch } = usePerformanceStandards(aid, ctx, true);

  if (data && data.rows.length === 0) return null;

  return (
    <div style={{ marginTop: 20, background: "var(--bg-surface)", border: "1px solid var(--border-soft)", borderRadius: "var(--radius)", padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>{t("reports.performance_standard.title")}</h3>
        <span className="caveat-badge">{t("reports.performance_standard.simulation_badge")}</span>
      </div>
      <p style={{ margin: "0 0 12px", fontSize: 12, color: "var(--text-tertiary)" }}>
        {t("reports.performance_standard.subtitle")}
      </p>
      {isLoading && <Skeleton height={80} />}
      {error && <ErrorBanner error={error} onRetry={() => refetch()} />}
      {data && data.rows.length > 0 && (
        <div style={{ width: "100%", overflowX: "auto" }}>
          <table style={SHARED_TABLE}>
            <thead>
              <tr style={{ background: "var(--bg-soft)" }}>
                <th style={th({ align: "left" })}>{t("common.route")}</th>
                <th style={th({ align: "left" })}>{t("reports.performance_standard.col.metric")}</th>
                <th style={th({ align: "right" })}>{t("reports.performance_standard.col.threshold")}</th>
                <th style={th({ align: "right" })}>{t("reports.performance_standard.col.actual")}</th>
                <th style={th({ align: "right" })}>{t("reports.performance_standard.col.achievement_rate")}</th>
                <th style={th({ align: "right" })}>{t("reports.performance_standard.col.estimate")}</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={`${r.route_code}-${r.metric_type}`} style={{ borderTop: "1px solid var(--border-soft)" }}>
                  <td style={{ ...td(), fontWeight: 500 }}><RouteLabel code={r.route_code} names={routeNames} /></td>
                  <td style={td()}>
                    {t(`reports.performance_standard.metric_label.${r.metric_type}`)}
                    {r.metric_scope === "agency" && (
                      <span style={{ display: "block", fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>
                        {t("reports.performance_standard.scope_agency_note")}
                      </span>
                    )}
                  </td>
                  <td style={td({ align: "right" })}>{fmtMetricValue(r.threshold_value, r.metric_type)}</td>
                  <td style={td({ align: "right" })}>{fmtMetricValue(r.actual_value, r.metric_type)}</td>
                  <td style={td({ align: "right" })}>{fmtAchievementRate(r.achievement_rate)}</td>
                  <td style={td({ align: "right" })}>{fmtEstimate(r.estimated_bonus_deduction)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && (
        <p style={{ margin: "12px 0 0", fontSize: "var(--text-xs)", color: "var(--text-tertiary)", fontStyle: "italic" }}>
          {data.disclaimer}
        </p>
      )}
    </div>
  );
}

