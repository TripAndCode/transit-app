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
import type { WeatherDelayBucket } from "../api/types";
import { Skeleton } from "./Skeleton";
import { ErrorBanner } from "./ErrorBanner";
import { delayColor } from "../styles/tokens";

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
          {data.buckets != null && data.buckets.length > 0 && (
            <WeatherBucketChart buckets={data.buckets} />
          )}
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

// Column height of the bar track, in px -- fixed rather than a CSS percentage
// so each bar's own height is a plain JS computation against it, matching
// this codebase's other hand-rolled charts (see DailyChart.tsx).
const BUCKET_TRACK_HEIGHT = 96;

/** Second chart in the panel (item 132): one bar per precipitation bucket
 *  from `pipeline.reports.weather.RAIN_BUCKET_LABELS`, additive alongside the
 *  wet/dry comparison above. Bar height and color both encode
 *  `avg_delay_sec` (a single sequential magnitude, per the dataviz skill --
 *  never a second/dual axis); the sample count is a direct label, not a
 *  second plotted scale. A bucket with no matched days/samples renders as a
 *  muted, minimal-height placeholder rather than being hidden, the same
 *  "zero is a real answer" convention `WeatherDelayGroup` already uses for
 *  the wet/dry sides. */
function WeatherBucketChart({ buckets }: { buckets: WeatherDelayBucket[] }) {
  const { t } = useTranslation();
  const maxSec = Math.max(1, ...buckets.map((b) => b.avg_delay_sec ?? 0));

  return (
    <div style={{ marginTop: 16 }}>
      <p style={{ margin: "0 0 8px", fontSize: 12, fontWeight: 500, color: "var(--text-secondary)" }}>
        {t("reports.weather_delay.buckets.title")}
      </p>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 16 }}>
        {buckets.map((b) => {
          const hasData = b.avg_delay_sec != null && b.samples > 0;
          const barHeight = hasData
            ? Math.max(4, (b.avg_delay_sec! / maxSec) * BUCKET_TRACK_HEIGHT)
            : 4;
          const color = hasData ? delayColor(b.avg_delay_sec! / 60) : "var(--border-soft)";
          const valueLabel = hasData
            ? fmtDelaySec(b.avg_delay_sec, t)
            : t("reports.weather_delay.buckets.no_data");

          return (
            <div
              key={b.label}
              style={{ display: "flex", flexDirection: "column", alignItems: "center", flex: 1, minWidth: 0 }}
            >
              <span style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>{valueLabel}</span>
              <div
                style={{
                  width: "100%",
                  height: BUCKET_TRACK_HEIGHT,
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "flex-end",
                }}
              >
                <div
                  role="img"
                  aria-label={t("reports.weather_delay.buckets.bar_aria", {
                    label: b.label,
                    value: valueLabel,
                    count: b.samples,
                  })}
                  title={t("reports.weather_delay.buckets.sample_count", { count: b.samples })}
                  style={{
                    width: "100%",
                    maxWidth: 24,
                    margin: "0 auto",
                    height: barHeight,
                    background: color,
                    opacity: hasData ? 1 : 0.6,
                    borderRadius: "4px 4px 0 0",
                  }}
                />
              </div>
              <span style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 6 }}>{b.label}</span>
              <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>
                {t("reports.weather_delay.buckets.sample_count", { count: b.samples })}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
