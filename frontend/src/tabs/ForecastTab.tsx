import { useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useRoutes, useForecastProfile, useForecastServices, useForecastDow } from "../api/hooks";
import { RoutePickerPill } from "../components/paramPills/RoutePickerPill";
import { Skeleton } from "../components/Skeleton";
import { ErrorBanner } from "../components/ErrorBanner";
import { delayColor } from "../styles/tokens";

const CHART_H = 150;
const PAD_L = 34;
const PAD_B = 22;
const WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;

type Bar = {
  /** stable react key + testid suffix grain */
  key: string | number;
  /** x-axis tick text; only drawn when axisShown */
  axisLabel: string;
  axisShown: boolean;
  /** readout title on hover, e.g. "8:00" or "金" */
  title: string;
  value: number | null;
  lowConf: boolean;
};

/** One calm SVG bar chart with full-column hover. Shared by the hourly profile
 *  and the day-of-week strip. The detail readout sits in a fixed line ABOVE the
 *  chart (never overlapping the bars); hovering a column shows that bar, and the
 *  default (no hover) shows the peak as a standing call-out. */
function BarChart({
  bars,
  axisMin,
  ariaLabel,
  testid,
  colW,
  lowConfLabel,
  peakLabel,
}: {
  bars: Bar[];
  axisMin: string;
  ariaLabel: string;
  testid: string;
  colW: number;
  lowConfLabel: string;
  peakLabel: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const populated = bars.filter((b) => b.value != null);
  const max = Math.max(...populated.map((b) => b.value as number), 1);

  // Peak bar drives the default readout (a standing "worst slot" call-out).
  let peakIdx = -1;
  let peakVal = -Infinity;
  bars.forEach((b, i) => {
    if (b.value != null && b.value > peakVal) {
      peakVal = b.value;
      peakIdx = i;
    }
  });
  const shownIdx = hover != null ? hover : peakIdx;
  const shown = shownIdx >= 0 ? bars[shownIdx] : null;

  const width = PAD_L + bars.length * colW;
  const height = CHART_H + PAD_B;
  const barW = colW - (colW > 40 ? 26 : 11); // slimmer bars, more breathing room

  return (
    <div>
      <div style={{ minHeight: 20, fontSize: 12, color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 7, marginBottom: 2 }}>
        {shown && shown.value != null && (
          <>
            {hover == null && (
              <span style={{ fontSize: 10, letterSpacing: "0.05em", textTransform: "uppercase", color: "var(--text-tertiary)" }}>
                {peakLabel}
              </span>
            )}
            <span style={{ fontVariantNumeric: "tabular-nums" }}>
              <b style={{ color: "var(--text-primary)" }}>{shown.title}</b> · {shown.value.toFixed(1)}
              {axisMin}
              {shown.lowConf ? ` (${lowConfLabel})` : ""}
            </span>
          </>
        )}
      </div>
      <svg
        width="100%"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={ariaLabel}
        style={{ maxWidth: width, display: "block", fontVariantNumeric: "tabular-nums" }}
      >
        <line x1={PAD_L} y1={CHART_H} x2={width} y2={CHART_H} stroke="var(--border-soft)" />
        <text x={4} y={11} fontSize={10} fill="var(--text-tertiary)">
          {max.toFixed(0)}
          {axisMin}
        </text>
        {bars.map((b, i) => {
          const x = PAD_L + i * colW;
          const cx = x + colW / 2;
          const bx = cx - barW / 2;
          const val = b.value;
          // Clamp ≥0: a negative pooled mean (early arrival) is an invalid rect height.
          const barH = val != null ? Math.max((val / max) * CHART_H, 0) : 0;
          const active = hover === i;
          return (
            <g key={b.key}>
              {active && (
                <rect x={x + 1} y={0} width={colW - 2} height={CHART_H} rx={4} fill="var(--accent-soft)" opacity={0.55} />
              )}
              {b.axisShown && (
                <text x={cx} y={height - 6} fontSize={10} fill="var(--text-tertiary)" textAnchor="middle">
                  {b.axisLabel}
                </text>
              )}
              {val != null && (
                <>
                  <rect
                    data-testid={`${testid}-bar`}
                    x={bx}
                    y={CHART_H - barH}
                    width={barW}
                    height={barH}
                    rx={3}
                    fill={delayColor(val)}
                    opacity={b.lowConf ? 0.45 : 1}
                  />
                  {b.lowConf && (
                    <circle
                      data-testid={`${testid}-bar-lowconf`}
                      cx={cx}
                      cy={Math.max(CHART_H - barH - 6, 4)}
                      r={2.5}
                      fill="var(--text-tertiary)"
                    />
                  )}
                </>
              )}
              {/* full-column transparent hit area: hover anywhere in the column */}
              <rect
                x={x}
                y={0}
                width={colW}
                height={CHART_H}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover((h) => (h === i ? null : h))}
              />
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// Control styled to match RoutePickerPill's metrics so the row reads as one set.
// Display a route-specific GTFS calendar id (e.g. "51_平日(共通)") as a short
// label ("平日") for the toggle. Strips a leading "NN_" block prefix and any
// trailing parenthetical; falls back to the raw value if that leaves nothing.
function cleanService(s: string): string {
  const cleaned = s.replace(/^\d+_/, "").replace(/[(（].*$/, "").trim();
  return cleaned || s;
}

export function ForecastTab() {
  const { t } = useTranslation();
  const { agencyId } = useParams();
  const aid = agencyId ? Number(agencyId) : null;

  const { data: routes } = useRoutes(aid);
  const [selectedRoute, setSelectedRoute] = useState<string | null>(null);
  // Default to the first available route (render-derived, no effect).
  const firstRoute = (routes ?? []).find((r) => r.route_code)?.route_code ?? null;
  const route = selectedRoute ?? firstRoute ?? "";

  // Service options are scoped to the selected route and arrive richest-first,
  // so the default is a full curve, not an often-sparse weekend service.
  const { data: servicesData } = useForecastServices(aid, route);
  const services = servicesData?.service_types ?? [];
  const [selectedService, setSelectedService] = useState<string | null>(null);
  // selectedService can be stale after a route change (its services differ), so
  // fall back to the richest available for the current route.
  const service =
    selectedService && services.includes(selectedService) ? selectedService : (services[0] ?? "");

  const { data, isPending, error, refetch } = useForecastProfile(aid, route, service);

  // Day-of-week strip is route-scoped (across the whole week, not per service).
  const { data: dow } = useForecastDow(aid, route);
  const dowHasData = dow != null && dow.days.some((d) => d.expected_avg_min != null);

  const allNull = data != null && data.hours.every((h) => h.expected_avg_min == null);

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: "0 auto" }}>
      <div style={{ fontSize: 12, color: "var(--text-tertiary)", letterSpacing: "0.04em" }}>
        {t("forecast.eyebrow")}
      </div>
      <h1 style={{ fontFamily: "var(--font-display)", fontWeight: 600, fontSize: 22, margin: "4px 0 16px" }}>
        {t("forecast.title")}
      </h1>

      {aid != null && (
        <div style={{ display: "flex", gap: 10, marginBottom: 22, alignItems: "center", flexWrap: "wrap" }}>
          <RoutePickerPill
            label={t("forecast.route_label")}
            value={route || null}
            agencyId={aid}
            placeholder={t("forecast.route_placeholder")}
            onChange={setSelectedRoute}
          />
          {services.length > 0 && (
            <div
              role="group"
              aria-label={t("forecast.service_label")}
              style={{ display: "inline-flex", height: 30, border: "1px solid var(--border-soft)", borderRadius: 6, overflow: "hidden" }}
            >
              {services.map((s, i) => {
                const sel = s === service;
                return (
                  <button
                    key={s}
                    type="button"
                    aria-pressed={sel}
                    onClick={() => setSelectedService(s)}
                    style={{
                      border: "none",
                      borderLeft: i ? "1px solid var(--border-soft)" : "none",
                      background: sel ? "var(--accent-soft)" : "var(--bg-soft)",
                      color: sel ? "var(--accent)" : "var(--text-secondary)",
                      fontWeight: sel ? 600 : 400,
                      fontSize: 12,
                      padding: "0 12px",
                      cursor: "pointer",
                    }}
                  >
                    {cleanService(s)}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      {!route && <p style={{ color: "var(--text-secondary)" }}>{t("forecast.pick_prompt")}</p>}

      {route && dowHasData && dow && (
        <section style={{ marginBottom: 28 }}>
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 2px" }}>{t("forecast.dow_title")}</h2>
          <p style={{ color: "var(--text-tertiary)", fontSize: 12, margin: "0 0 8px" }}>
            {t("forecast.dow_caption")}
          </p>
          <BarChart
            testid="dow"
            colW={64}
            bars={dow.days.map((d) => ({
              key: d.dow,
              axisLabel: t(`forecast.dow_${WEEK[d.dow - 1]}`),
              axisShown: true,
              title: t(`forecast.dow_${WEEK[d.dow - 1]}`),
              value: d.expected_avg_min,
              lowConf: d.low_confidence,
            }))}
            axisMin={t("forecast.axis_min")}
            ariaLabel={t("forecast.dow_svg_aria")}
            lowConfLabel={t("forecast.low_confidence")}
            peakLabel={t("forecast.peak")}
          />
          <LowConfLegend label={t("forecast.low_confidence")} />
          <Disclaimer text={dow.disclaimer} />
        </section>
      )}

      {route && (
        <section>
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 8px" }}>{t("forecast.hourly_title")}</h2>
          {isPending && <Skeleton height={CHART_H + PAD_B} />}
          {error && <ErrorBanner error={error} onRetry={() => refetch()} />}
          {data && allNull && <p style={{ color: "var(--text-secondary)" }}>{t("forecast.no_data")}</p>}
          {data && !allNull && (
            <>
              <BarChart
                testid="forecast"
                colW={30}
                bars={data.hours.map((h) => ({
                  key: h.hour,
                  axisLabel: String(h.hour),
                  axisShown: h.hour % 6 === 0,
                  title: `${h.hour}:00`,
                  value: h.expected_avg_min,
                  lowConf: h.low_confidence,
                }))}
                axisMin={t("forecast.axis_min")}
                ariaLabel={t("forecast.svg_aria")}
                lowConfLabel={t("forecast.low_confidence")}
                peakLabel={t("forecast.peak")}
              />
              <LowConfLegend label={t("forecast.low_confidence")} />
              <Disclaimer text={data.disclaimer} />
            </>
          )}
        </section>
      )}
    </div>
  );
}

function LowConfLegend({ label }: { label: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, margin: "8px 0", fontSize: 12, color: "var(--text-tertiary)" }}>
      <span aria-hidden style={{ width: 5, height: 5, borderRadius: "50%", background: "var(--text-tertiary)" }} />
      {label}
    </div>
  );
}

function Disclaimer({ text }: { text: string }) {
  return (
    <p style={{ color: "var(--text-secondary)", fontSize: 13, maxWidth: 640, lineHeight: 1.5 }}>{text}</p>
  );
}
