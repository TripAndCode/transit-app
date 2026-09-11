/**
 * Fourth metric panel shown alongside the `on_time` report (item 130):
 * observed rain-vs-dry average delay from `useWeatherDelay` (item 129).
 * `available: false` is an expected, common configuration -- most agencies
 * have no representative weather station mapped yet -- so it renders one
 * calm line, never `ErrorBanner`/red styling. The server's own
 * `disclaimer`/`attribution` strings carry the correlation-not-causation and
 * source-attribution wording and are rendered verbatim rather than restated.
 */
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { useWeatherDelay } from "../api/hooks";
import type { RangeCtx } from "../api/rangeContext";
import { Skeleton } from "./Skeleton";
import { ErrorBanner } from "./ErrorBanner";

function fmtDelaySec(v: number | null, t: TFunction): string {
  if (v == null) return "—";
  return `${v.toFixed(1)}${t("common.unit_sec")}`;
}

function fmtDeltaSec(v: number | null, t: TFunction): string {
  if (v == null) return "—";
  return t("common.unit_sec_signed", { sign: v < 0 ? "-" : "+", value: Math.abs(v).toFixed(1) });
}

export function WeatherDelayPanel({ aid, ctx }: { aid: number; ctx: RangeCtx }) {
  const { t } = useTranslation();
  const { data, isLoading, error, refetch } = useWeatherDelay(aid, ctx, true);

  return (
    <div style={{ marginTop: 20, background: "var(--bg-surface)", border: "1px solid var(--border-soft)", borderRadius: "var(--radius)", padding: 16 }}>
      <h3 style={{ margin: "0 0 4px", fontSize: 14 }}>{t("reports.weather_delay.title")}</h3>
      <p style={{ margin: "0 0 12px", fontSize: 12, color: "var(--text-tertiary)" }}>
        {t("reports.weather_delay.subtitle")}
      </p>
      {isLoading && <Skeleton height={80} />}
      {error && <ErrorBanner error={error} onRetry={() => refetch()} />}
      {data && !data.available && (
        <p style={{ margin: 0, fontSize: 13, color: "var(--text-secondary)" }}>
          {t("reports.weather_delay.unavailable")}
        </p>
      )}
      {data && data.available && (
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 4 }}>
            <span style={{ fontSize: 13, fontWeight: 500 }}>
              {t("reports.weather_delay.station_label", { name: data.station?.station_name ?? "" })}
            </span>
            {data.low_confidence && (
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
                {t("reports.weather_delay.low_confidence_badge")}
              </span>
            )}
          </div>
          {data.station?.note && (
            <p style={{ margin: "0 0 12px", fontSize: 12, color: "var(--text-tertiary)" }}>{data.station.note}</p>
          )}
          <div style={{ width: "100%", overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ background: "var(--bg-soft)" }}>
                  <th style={th("left")}>{t("reports.weather_delay.col.condition")}</th>
                  <th style={th("right")}>{t("reports.weather_delay.col.avg_delay")}</th>
                  <th style={th("right")}>{t("reports.weather_delay.col.days")}</th>
                  <th style={th("right")}>{t("reports.weather_delay.col.samples")}</th>
                </tr>
              </thead>
              <tbody>
                <tr style={{ borderTop: "1px solid var(--border-soft)" }}>
                  <td style={{ ...td(), fontWeight: 500 }}>{t("reports.weather_delay.row.wet")}</td>
                  <td style={{ ...td(), textAlign: "right" }}>{fmtDelaySec(data.wet.avg_delay_sec, t)}</td>
                  <td style={{ ...td(), textAlign: "right" }}>{data.wet.days.toLocaleString()}</td>
                  <td style={{ ...td(), textAlign: "right" }}>{data.wet.samples.toLocaleString()}</td>
                </tr>
                <tr style={{ borderTop: "1px solid var(--border-soft)" }}>
                  <td style={{ ...td(), fontWeight: 500 }}>{t("reports.weather_delay.row.dry")}</td>
                  <td style={{ ...td(), textAlign: "right" }}>{fmtDelaySec(data.dry.avg_delay_sec, t)}</td>
                  <td style={{ ...td(), textAlign: "right" }}>{data.dry.days.toLocaleString()}</td>
                  <td style={{ ...td(), textAlign: "right" }}>{data.dry.samples.toLocaleString()}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p style={{ margin: "12px 0 0", fontSize: 13, color: "var(--text-secondary)" }}>
            {data.delta_sec == null
              ? t("reports.weather_delay.delta_unavailable")
              : t("reports.weather_delay.delta_label", { value: fmtDeltaSec(data.delta_sec, t) })}
          </p>
          <p style={{ margin: "12px 0 0", fontSize: 11, color: "var(--text-tertiary)", fontStyle: "italic" }}>
            {data.disclaimer}
          </p>
          <p style={{ margin: "4px 0 0", fontSize: 11, color: "var(--text-tertiary)" }}>{data.attribution}</p>
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
