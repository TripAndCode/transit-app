import { useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useRoutes, useForecastProfile, useForecastServices } from "../api/hooks";
import { RoutePickerPill } from "../components/paramPills/RoutePickerPill";
import { Skeleton } from "../components/Skeleton";
import { ErrorBanner } from "../components/ErrorBanner";
import { delayColor } from "../styles/tokens";
import type { ForecastProfileHour } from "../api/types";

const CHART_H = 160;
const COL_W = 30;
const PAD_L = 36;
const PAD_B = 22;

function HourlyChart({
  hours,
  axisMin,
  ariaLabel,
}: {
  hours: ForecastProfileHour[];
  axisMin: string;
  ariaLabel: string;
}) {
  const populated = hours.filter((h) => h.expected_avg_min != null);
  const max = Math.max(...populated.map((h) => h.expected_avg_min as number), 1);
  const width = PAD_L + 24 * COL_W;
  const height = CHART_H + PAD_B;

  return (
    <svg
      width="100%"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={ariaLabel}
      style={{ maxWidth: width, display: "block" }}
    >
      <line x1={PAD_L} y1={CHART_H} x2={width} y2={CHART_H} stroke="var(--border-subtle)" />
      <text x={4} y={12} fontSize={10} fill="var(--text-tertiary)">
        {max.toFixed(0)}
        {axisMin}
      </text>
      {hours.map((h) => {
        const x = PAD_L + h.hour * COL_W;
        const labelled = h.hour % 6 === 0;
        const val = h.expected_avg_min;
        const barW = COL_W - 8;
        const barH = val != null ? (val / max) * CHART_H : 0;
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
                  y={CHART_H - barH}
                  width={barW}
                  height={barH}
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
                    cy={Math.max(CHART_H - barH - 6, 3)}
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
  // Default to the first available route (render-derived, no effect); the
  // picker stays in control once the user chooses.
  const firstRoute = (routes ?? []).find((r) => r.route_code)?.route_code ?? null;
  const route = selectedRoute ?? firstRoute ?? "";

  // Service options come from the agency's real agg_route_hour labels, not
  // hardcoded bare values (which match almost no agency).
  const { data: servicesData } = useForecastServices(aid);
  const services = servicesData?.service_types ?? [];
  const [selectedService, setSelectedService] = useState<string | null>(null);
  const service = selectedService ?? services[0] ?? "";

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

      {aid != null && (
        <div style={{ display: "flex", gap: 16, marginBottom: 20, fontSize: 13, color: "var(--text-secondary)", alignItems: "center", flexWrap: "wrap" }}>
          <RoutePickerPill
            label={t("forecast.route_label")}
            value={route || null}
            agencyId={aid}
            placeholder={t("forecast.route_placeholder")}
            onChange={setSelectedRoute}
          />
          <label>
            {t("forecast.service_label")}{" "}
            <select
              value={service}
              onChange={(e) => setSelectedService(e.target.value)}
              disabled={services.length === 0}
            >
              {services.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {!route && <p style={{ color: "var(--text-secondary)" }}>{t("forecast.pick_prompt")}</p>}
      {route && isPending && <Skeleton height={CHART_H + PAD_B} />}
      {route && error && <ErrorBanner error={error} onRetry={() => refetch()} />}
      {route && data && allNull && <p style={{ color: "var(--text-secondary)" }}>{t("forecast.no_data")}</p>}
      {route && data && !allNull && (
        <>
          <HourlyChart hours={data.hours} axisMin={t("forecast.axis_min")} ariaLabel={t("forecast.svg_aria")} />
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
