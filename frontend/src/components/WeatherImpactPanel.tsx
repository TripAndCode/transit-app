/**
 * Observed wet-day vs dry-day service panel (item 105), rendered alongside
 * the `on_time` report the same way `HeadwayQualityPanel` is.
 *
 * Both sides are days that already happened, split by the observed daily
 * precipitation total at this agency's representative JMA station -- a
 * DESCRIPTIVE comparison, never a forecast and never a rain-attributed
 * cause (see pipeline/reports/weather.py). The observed-not-forecast badge,
 * the confounder caveat, and the server's own JMA attribution text all stay
 * attached to these figures.
 *
 * Renders nothing when this agency has no observed precipitation in range
 * (nothing ingested yet, or temperature-only rows) -- same
 * "not configured yet, so no panel" convention as
 * PerformanceStandardPanel.
 */
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { useWeatherImpact } from "../api/hooks";
import type { RangeCtx } from "../api/rangeContext";
import type { WeatherImpactSide } from "../api/types";
import { Skeleton } from "./Skeleton";
import { ErrorBanner } from "./ErrorBanner";

function fmtPct(v: number | null): string {
  return v == null ? "—" : `${v.toFixed(1)}%`;
}

function fmtMin(v: number | null, t: TFunction): string {
  return v == null ? "—" : `${v.toFixed(2)} ${t("common.unit_min")}`;
}

function sign(v: number): string {
  // "−" (U+2212) for negatives, matching PerformanceStandardPanel's own
  // signed figures rather than a hyphen.
  return v > 0 ? "+" : v < 0 ? "−" : "";
}

function fmtGapPt(v: number | null, t: TFunction): string {
  return v == null ? "—" : `${sign(v)}${Math.abs(v).toFixed(1)} ${t("reports.weather_impact.unit_pt")}`;
}

function fmtGapMin(v: number | null, t: TFunction): string {
  return v == null
    ? "—"
    : t("common.unit_min_signed", { sign: sign(v), value: Math.abs(v).toFixed(2) });
}

export function WeatherImpactPanel({ aid, ctx }: { aid: number; ctx: RangeCtx }) {
  const { t } = useTranslation();
  const { data, isLoading, error, refetch } = useWeatherImpact(aid, ctx, true);

  if (data && data.station_id == null) return null;

  const sideRow = (labelKey: string, side: WeatherImpactSide) => (
    <tr style={{ borderTop: "1px solid var(--border-soft)" }}>
      <td style={{ ...td(), fontWeight: 500 }}>{t(labelKey)}</td>
      <td style={{ ...td(), textAlign: "right" }}>{side.days.toLocaleString()}</td>
      <td style={{ ...td(), textAlign: "right" }}>{fmtPct(side.on_time_pct)}</td>
      <td style={{ ...td(), textAlign: "right" }}>{fmtMin(side.avg_delay_min, t)}</td>
    </tr>
  );

  return (
    <div style={{ marginTop: 20, background: "var(--bg-surface)", border: "1px solid var(--border-soft)", borderRadius: "var(--radius)", padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>{t("reports.weather_impact.title")}</h3>
        <span
          style={{
            fontSize: 11,
            fontWeight: 500,
            color: "var(--text-secondary)",
            background: "var(--bg-soft)",
            border: "1px solid var(--border-soft)",
            borderRadius: 999,
            padding: "2px 8px",
          }}
        >
          {t("reports.weather_impact.observed_badge")}
        </span>
      </div>
      {isLoading && <Skeleton height={80} />}
      {error && <ErrorBanner error={error} onRetry={() => refetch()} />}
      {data && (
        <>
          <p style={{ margin: "0 0 12px", fontSize: 12, color: "var(--text-tertiary)" }}>
            {t("reports.weather_impact.subtitle", { threshold: data.wet_threshold_mm })}
          </p>
          <div style={{ width: "100%", overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ background: "var(--bg-soft)" }}>
                  <th style={th("left")}>{t("reports.weather_impact.col.group")}</th>
                  <th style={th("right")}>{t("reports.weather_impact.col.days")}</th>
                  <th style={th("right")}>{t("reports.weather_impact.col.on_time")}</th>
                  <th style={th("right")}>{t("reports.weather_impact.col.avg_delay")}</th>
                </tr>
              </thead>
              <tbody>
                {sideRow("reports.weather_impact.row.wet", data.wet)}
                {sideRow("reports.weather_impact.row.dry", data.dry)}
                <tr style={{ borderTop: "1px solid var(--border-soft)" }}>
                  <td style={{ ...td(), color: "var(--text-secondary)" }}>{t("reports.weather_impact.row.gap")}</td>
                  <td style={{ ...td(), textAlign: "right" }}>—</td>
                  <td style={{ ...td(), textAlign: "right" }}>{fmtGapPt(data.on_time_gap_pt, t)}</td>
                  <td style={{ ...td(), textAlign: "right" }}>{fmtGapMin(data.avg_delay_gap_min, t)}</td>
                </tr>
              </tbody>
            </table>
          </div>
          {!data.available && (
            <p style={{ margin: "12px 0 0", fontSize: 12, color: "var(--text-secondary)" }}>
              {t("reports.weather_impact.insufficient", { min: data.min_days_per_bucket })}
            </p>
          )}
          <p style={{ margin: "12px 0 0", fontSize: 11, color: "var(--text-tertiary)" }}>
            {t("reports.weather_impact.caveat")}
          </p>
          <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--text-tertiary)" }}>
            {t("reports.weather_impact.coverage", {
              observed: data.observed_days,
              total: data.observed_days + data.unobserved_days,
              pct: data.coverage_pct == null ? "—" : data.coverage_pct.toFixed(1),
            })}
            {data.station_name != null && ` ${t("reports.weather_impact.station", { station: data.station_name })}`}
            {data.latest_observed_date != null &&
              ` ${t("reports.weather_impact.latest_observed", { date: data.latest_observed_date })}`}
          </p>
          <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--text-tertiary)", fontStyle: "italic" }}>
            {data.attribution}
          </p>
        </>
      )}
    </div>
  );
}

const th = (align: "left" | "right"): React.CSSProperties => ({
  padding: "8px 10px",
  textAlign: align,
  fontWeight: 500,
  color: "var(--text-secondary)",
  fontSize: 12,
});
const td = (): React.CSSProperties => ({
  padding: "6px 10px",
  fontSize: 13,
});
