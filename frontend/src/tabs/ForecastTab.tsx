import { useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useRoutes, useForecastProfile } from "../api/hooks";
import { Skeleton } from "../components/Skeleton";
import { ErrorBanner } from "../components/ErrorBanner";
import { delayColor } from "../styles/tokens";
import type { ForecastProfileHour } from "../api/types";

// Service-type values are a query contract (raw GTFS service keys stored in
// agg_route_hour), labelled in the active locale — same as TabFilterBar.
const SERVICES = [
  { value: "平日", labelKey: "filters.service.weekday" }, // i18n-ignore: query contract
  { value: "土日祝", labelKey: "filters.service.weekend" }, // i18n-ignore: query contract
];

const CHART_H = 160;
const COL_W = 30;
const PAD_L = 36;
const PAD_B = 22;

function HourlyChart({ hours, axisMin }: { hours: ForecastProfileHour[]; axisMin: string }) {
  const populated = hours.filter((h) => h.expected_avg_min != null);
  const max = Math.max(...populated.map((h) => h.expected_avg_min as number), 1);
  const width = PAD_L + 24 * COL_W;
  const height = CHART_H + PAD_B;

  return (
    <svg
      width="100%"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      style={{ maxWidth: width, display: "block" }}
    >
      {/* baseline */}
      <line x1={PAD_L} y1={CHART_H} x2={width} y2={CHART_H} stroke="var(--border-subtle)" />
      {/* y reference: the max */}
      <text x={4} y={12} fontSize={10} fill="var(--text-tertiary)">
        {max.toFixed(0)}
        {axisMin}
      </text>
      {hours.map((h) => {
        const x = PAD_L + h.hour * COL_W;
        const labelled = h.hour % 6 === 0;
        const val = h.expected_avg_min;
        const barW = COL_W - 8;
        return (
          <g key={h.hour}>
            {labelled && (
              <text x={x + barW / 2} y={height - 6} fontSize={10} fill="var(--text-tertiary)" textAnchor="middle">
                {h.hour}
              </text>
            )}
            {val != null && (
              <>
                <rect
                  data-testid="forecast-bar"
                  x={x}
                  y={CHART_H - (val / max) * CHART_H}
                  width={barW}
                  height={(val / max) * CHART_H}
                  rx={2}
                  fill={delayColor(val)}
                  opacity={h.low_confidence ? 0.4 : 1}
                >
                  <title>{`${h.hour}:00 — ${val.toFixed(1)}${axisMin}`}</title>
                </rect>
                {h.low_confidence && (
                  <circle
                    data-testid="forecast-bar-lowconf"
                    cx={x + barW / 2}
                    cy={CHART_H - (val / max) * CHART_H - 6}
                    r={2.5}
                    fill="var(--text-tertiary)"
                  />
                )}
              </>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export function ForecastTab() {
  const { t } = useTranslation();
  const { agencyId } = useParams();
  const aid = agencyId ? Number(agencyId) : null;
  const { data: routes } = useRoutes(aid);
  const [selectedRoute, setSelectedRoute] = useState<string | null>(null);
  const [service, setService] = useState(SERVICES[0].value);

  // Deduplicate route options by code (an agency can list variants per code).
  const seen = new Set<string>();
  const routeOptions: { code: string; label: string }[] = [];
  for (const r of routes ?? []) {
    if (!r.route_code || seen.has(r.route_code)) continue;
    seen.add(r.route_code);
    routeOptions.push({
      code: r.route_code,
      label: r.route_short_name || r.route_long_name || r.route_code,
    });
  }
  // Default to the first route once the list loads (render-derived, no effect).
  const route = selectedRoute ?? routeOptions[0]?.code ?? "";

  const { data, isPending, error, refetch } = useForecastProfile(aid, route, service);

  const allNull = data != null && data.hours.every((h) => h.expected_avg_min == null);

  return (
    <div style={{ padding: 24, maxWidth: 920, margin: "0 auto" }}>
      <div style={{ fontSize: 12, color: "var(--text-tertiary)", letterSpacing: "0.04em" }}>
        {t("forecast.eyebrow")}
      </div>
      <h1 style={{ fontFamily: "var(--font-display)", fontWeight: 600, fontSize: 22, margin: "4px 0 16px" }}>
        {t("forecast.title")}
      </h1>

      <div style={{ display: "flex", gap: 16, marginBottom: 20, fontSize: 13, color: "var(--text-secondary)", flexWrap: "wrap" }}>
        <label>
          {t("forecast.route_label")}{" "}
          <select value={route} onChange={(e) => setSelectedRoute(e.target.value)} disabled={routeOptions.length === 0}>
            {routeOptions.map((o) => (
              <option key={o.code} value={o.code}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("forecast.service_label")}{" "}
          <select value={service} onChange={(e) => setService(e.target.value)}>
            {SERVICES.map((s) => (
              <option key={s.value} value={s.value}>
                {t(s.labelKey)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {!route && <p style={{ color: "var(--text-secondary)" }}>{t("forecast.pick_prompt")}</p>}
      {route && isPending && <Skeleton height={CHART_H + PAD_B} />}
      {route && error && <ErrorBanner error={error} onRetry={() => refetch()} />}
      {route && data && allNull && <p style={{ color: "var(--text-secondary)" }}>{t("forecast.no_data")}</p>}
      {route && data && !allNull && (
        <>
          <HourlyChart hours={data.hours} axisMin={t("forecast.axis_min")} />
          <div style={{ display: "flex", alignItems: "center", gap: 6, margin: "8px 0", fontSize: 12, color: "var(--text-tertiary)" }}>
            <span aria-hidden style={{ width: 5, height: 5, borderRadius: "50%", background: "var(--text-tertiary)" }} />
            {t("forecast.low_confidence")}
          </div>
          <p style={{ color: "var(--text-secondary)", fontSize: 13, maxWidth: 640, lineHeight: 1.5 }}>
            {data.disclaimer}
          </p>
        </>
      )}
    </div>
  );
}
