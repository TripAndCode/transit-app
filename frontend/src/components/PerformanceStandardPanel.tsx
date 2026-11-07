/**
 * Per-route "minimum performance standard" bonus/malus simulation panel,
 * comparing each configured threshold against the route's on-time rate, its
 * Excess Waiting Time or the agency's vehicle-km-delivered rate, and saying
 * whether each figure meets its target. Renders nothing until it knows this
 * agency has configured `route_performance_standards` rows (see
 * pipeline/reports/performance_standard.py) -- admins write them in the agency
 * drawer, and most routes have none, so the panel never claims space it may
 * not need.
 *
 * An on-time target is judged on the Routes table's own figure under the
 * page's on-time definition, which the panel names beneath the table: a
 * target means nothing without the definition it was read against.
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
import { ErrorBanner } from "./ErrorBanner";
import { OnTimeDefinition } from "./DefinitionMetaBlock";
import { fixedTenth, formatDuration, roundTenth } from "../utils/format";

function fmtMetricValue(v: number | null, metricType: PerformanceStandardRow["metric_type"]): string {
  if (v == null) return "—";
  return metricType === "ewt_sec" ? formatDuration(v) : `${fixedTenth(v)}%`;
}

function fmtAchievementRate(v: number | null): string {
  return v == null ? "—" : `${fixedTenth((v * 100))}%`;
}

function fmtEstimate(v: number | null): string {
  if (v == null) return "—";
  const r = roundTenth(v);
  const sign = r > 0 ? "+" : r < 0 ? "−" : "";
  return `${sign}${fixedTenth(Math.abs(r))}`;
}

function Judgement({ meets }: { meets: boolean | null }) {
  const { t } = useTranslation();
  if (meets == null) return <>—</>;
  return (
    <span className={`standard-chip standard-chip--${meets ? "meets" : "below"}`}>
      {t(meets ? "reports.performance_standard.meets" : "reports.performance_standard.below")}
    </span>
  );
}

/** `route` narrows the panel to one route's targets, for that route's own page. */
export function PerformanceStandardPanel({ aid, ctx, route }: { aid: number; ctx: Scope; route?: string }) {
  const { t } = useTranslation();
  const routeNames = useRouteNames(aid);
  const { data, error, refetch } = usePerformanceStandards(aid, ctx, true);
  const rows = data?.rows.filter((r) => route == null || r.route_code === route) ?? [];

  if (!error && rows.length === 0) return null;
  const onTimeDefinition = rows.some((r) => r.metric_type === "on_time_pct") ? data?.on_time_definition : null;

  return (
    <div style={{ marginTop: 20, background: "var(--bg-surface)", border: "1px solid var(--border-soft)", borderRadius: "var(--radius)", padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>{t("reports.performance_standard.title")}</h3>
        <span className="caveat-badge">{t("reports.performance_standard.simulation_badge")}</span>
      </div>
      <p style={{ margin: "0 0 12px", fontSize: 12, color: "var(--text-tertiary)" }}>
        {t("reports.performance_standard.subtitle")}
      </p>
      {error && <ErrorBanner error={error} onRetry={() => refetch()} />}
      {rows.length > 0 && (
        <div style={{ width: "100%", overflowX: "auto" }}>
          <table style={SHARED_TABLE}>
            <thead>
              <tr style={{ background: "var(--bg-soft)" }}>
                <th style={th({ align: "left" })}>{t("common.route")}</th>
                <th style={th({ align: "left" })}>{t("reports.performance_standard.col.metric")}</th>
                <th style={th({ align: "right" })}>{t("reports.performance_standard.col.threshold")}</th>
                <th style={th({ align: "right" })}>{t("reports.performance_standard.col.actual")}</th>
                <th style={th({ align: "left" })}>{t("reports.performance_standard.col.judgement")}</th>
                <th style={th({ align: "right" })}>{t("reports.performance_standard.col.achievement_rate")}</th>
                <th style={th({ align: "right" })}>{t("reports.performance_standard.col.estimate")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.route_code}-${r.metric_type}`} style={{ borderTop: "1px solid var(--border-soft)" }}>
                  <td style={{ ...td(), fontWeight: 500 }}><RouteLabel code={r.route_code} names={routeNames} compact /></td>
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
                  <td style={td()}><Judgement meets={r.meets} /></td>
                  <td style={td({ align: "right" })}>{fmtAchievementRate(r.achievement_rate)}</td>
                  <td style={td({ align: "right" })}>{fmtEstimate(r.estimated_bonus_deduction)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {onTimeDefinition && (
        <div className="standard-definition">
          <p>
            {onTimeDefinition.preset == null || onTimeDefinition.preset === "custom"
              ? t("reports.performance_standard.judged_on_custom")
              : t("reports.performance_standard.judged_on", { preset: onTimeDefinition.preset })}
          </p>
          <OnTimeDefinition definition={onTimeDefinition} />
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
