/**
 * Second metric panel shown alongside the `on_time` report (item 94):
 * Excess Waiting Time, spacing coefficient of variation, and long-gap rate
 * for this agency's routes classified high-frequency by
 * `agg_route_headway.is_high_frequency` (pipeline/headways.py). Renders
 * nothing extra for a non-high-frequency route -- such routes simply never
 * appear in `rows` (see pipeline/reports/headway_quality.py), and the
 * `on_time` table above this panel is completely unaffected either way.
 */
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { useHeadwayQuality } from "../api/hooks";
import { useRouteNames } from "../api/useRouteNames";
import type { RangeCtx } from "../api/rangeContext";
import { Skeleton } from "./Skeleton";
import { ErrorBanner } from "./ErrorBanner";

function fmtSignedMin(sec: number | null, t: TFunction): string {
  if (sec == null) return "—";
  const m = Math.round(sec / 60);
  if (m === 0) return `0 ${t("common.unit_min")}`;
  return t("common.unit_min_signed", { sign: sec < 0 ? "-" : "+", value: Math.abs(m) });
}

function fmtCov(v: number | null): string {
  return v == null ? "—" : v.toFixed(2);
}

function fmtPct(v: number | null): string {
  return v == null ? "—" : `${(v * 100).toFixed(1)}%`;
}

export function HeadwayQualityPanel({ aid, ctx }: { aid: number; ctx: RangeCtx }) {
  const { t } = useTranslation();
  const { format: formatRoute } = useRouteNames(aid);
  const { data, isLoading, error, refetch } = useHeadwayQuality(aid, ctx, true);

  return (
    <div style={{ marginTop: 20, background: "var(--bg-surface)", border: "1px solid var(--border-soft)", borderRadius: "var(--radius)", padding: 16 }}>
      <h3 style={{ margin: "0 0 4px", fontSize: 14 }}>{t("reports.headway_quality.title")}</h3>
      <p style={{ margin: "0 0 12px", fontSize: 12, color: "var(--text-tertiary)" }}>{t("reports.headway_quality.subtitle")}</p>
      {isLoading && <Skeleton height={80} />}
      {error && <ErrorBanner error={error} onRetry={() => refetch()} />}
      {data && data.rows.length === 0 && (
        <p style={{ margin: 0, fontSize: 13, color: "var(--text-secondary)" }}>{t("reports.headway_quality.empty")}</p>
      )}
      {data && data.rows.length > 0 && (
        <div style={{ width: "100%", overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "var(--bg-soft)" }}>
                <th style={th("left")}>{t("reports.headway_quality.col.route")}</th>
                <th style={th("right")}>{t("reports.headway_quality.col.ewt")}</th>
                <th style={th("right")}>{t("reports.headway_quality.col.cov")}</th>
                <th style={th("right")}>{t("reports.headway_quality.col.long_gap_rate")}</th>
                <th style={th("right")}>{t("reports.headway_quality.col.samples")}</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.route_code} style={{ borderTop: "1px solid var(--border-soft)" }}>
                  <td style={{ ...td(), fontWeight: 500 }}>{formatRoute(r.route_code)}</td>
                  <td style={{ ...td(), textAlign: "right" }}>{fmtSignedMin(r.ewt_sec, t)}</td>
                  <td style={{ ...td(), textAlign: "right" }}>{fmtCov(r.cov)}</td>
                  <td style={{ ...td(), textAlign: "right" }}>{fmtPct(r.long_gap_rate)}</td>
                  <td style={{ ...td(), textAlign: "right" }}>{r.samples.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
