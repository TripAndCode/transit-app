import { useState } from "react";
import { useTranslation } from "react-i18next";
import { delayColor } from "../../styles/tokens";
import type { TrendDay } from "../../api/types";

type Props = { days: TrendDay[]; height?: number };

export function DailyChart({ days, height = 240 }: Props) {
  const { t } = useTranslation();
  const [rawHover, setHover] = useState<number | null>(null);

  // If the data shrinks (filter narrowed), a stale hover index would
  // dereference out-of-bounds — clamp during render instead of an effect.
  const hover = rawHover != null && rawHover < days.length ? rawHover : null;
  const W = 760;
  const H = height;
  const padL = 44;
  const padR = 12;
  const padT = 12;
  const padB = 28;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  const avgs = days.map((d) => d.avg_min ?? 0);
  const samples = days.map((d) => d.samples ?? 0);
  const stats = {
    maxAvg: Math.max(1, ...avgs),
    maxSamples: Math.max(1, ...samples),
  };

  if (!days.length) {
    return (
      <div style={{ padding: 24, color: "var(--text-tertiary)", textAlign: "center" }}>
        {t("reports.daily.empty")}
      </div>
    );
  }

  const stepX = innerW / Math.max(1, days.length - 1);
  const linePts = days.map((d, i) => {
    const x = padL + i * stepX;
    const y = padT + innerH - ((d.avg_min ?? 0) / stats.maxAvg) * innerH * 0.65;
    return [x, y] as [number, number];
  });
  // Trailing 7-day average sits ALONGSIDE the raw line (not a replacement) —
  // a low-traffic day's noisy raw figure is easier to read against a smooth
  // reference. Absent entirely for week/month-bucketed series and for
  // cached responses from before this field existed, so only draw it when
  // at least one day actually has a value; missing individual days (rare —
  // see compute_trend_series's docstring) are simply skipped rather than
  // breaking the line at 0.
  const smoothedPts = days
    .map((d, i) =>
      d.avg_min_smoothed != null
        ? ([padL + i * stepX, padT + innerH - (d.avg_min_smoothed / stats.maxAvg) * innerH * 0.65] as [
            number,
            number,
          ])
        : null,
    )
    .filter((p): p is [number, number] => p !== null);
  const hasSmoothed = smoothedPts.length > 0;

  return (
    <div style={{ position: "relative", width: "100%", overflowX: "auto" }}>
      <svg width={W} height={H} role="img" aria-label={t("reports.daily.svg_aria")} style={{ display: "block" }}>
        {/* Y axis grid */}
        {[0.25, 0.5, 0.75].map((f) => {
          const y = padT + innerH * 0.35 + (1 - f) * innerH * 0.65;
          return (
            <g key={f}>
              <line x1={padL} x2={W - padR} y1={y} y2={y} stroke="var(--border-soft)" strokeDasharray="2 4" />
              <text x={6} y={y + 4} fontSize="10" fill="var(--text-tertiary)">
                {(stats.maxAvg * f).toFixed(1)}m
              </text>
            </g>
          );
        })}
        {/* Sample-count bars (lower 30% of chart) */}
        {days.map((d, i) => {
          const x = padL + i * stepX - Math.max(1, stepX * 0.4);
          const w = Math.max(2, stepX * 0.8);
          const h = ((d.samples ?? 0) / stats.maxSamples) * innerH * 0.3;
          const y = padT + innerH - h;
          return (
            <rect
              key={`bar-${i}`}
              x={x}
              y={y}
              width={w}
              height={h}
              fill="var(--accent-soft)"
              opacity={0.7}
            />
          );
        })}
        {/* Trailing 7-day average — drawn under the raw line so the raw
            line + dots stay the primary, foreground signal. */}
        {hasSmoothed && (
          <polyline
            points={smoothedPts.map((p) => p.join(",")).join(" ")}
            fill="none"
            stroke="var(--text-secondary)"
            strokeWidth="1.5"
            strokeDasharray="4 3"
            opacity={0.8}
          />
        )}
        {/* Line */}
        <polyline
          points={linePts.map((p) => p.join(",")).join(" ")}
          fill="none"
          stroke="var(--accent)"
          strokeWidth="2"
        />
        {/* Points + hover targets */}
        {days.map((d, i) => {
          const [x, y] = linePts[i];
          const c = delayColor(d.avg_min ?? 0);
          return (
            <g key={`pt-${i}`}>
              {/* `fill` goes in `style`, not the SVG presentation attribute:
                  delayColor()'s severe tier is now the literal "var(--delay-severe)",
                  and var() only resolves in a CSS property, not a presentation attr. */}
              <circle
                cx={x}
                cy={y}
                r={hover === i ? 5 : 3}
                style={{ fill: c, stroke: "var(--bg-surface)" }}
                strokeWidth="1.5"
              />
              <rect
                x={padL + i * stepX - stepX / 2}
                y={padT}
                width={Math.max(1, stepX)}
                height={innerH}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover((v) => (v === i ? null : v))}
              />
            </g>
          );
        })}
        {/* X axis tick labels (every Nth day) */}
        {days.map((d, i) => {
          const stride = Math.max(1, Math.floor(days.length / 8));
          if (i % stride !== 0 && i !== days.length - 1) return null;
          const x = padL + i * stepX;
          return (
            <text
              key={`xt-${i}`}
              x={x}
              y={H - 8}
              fontSize="10"
              fill="var(--text-tertiary)"
              textAnchor="middle"
            >
              {d.date.slice(5)}
            </text>
          );
        })}
      </svg>
      {hasSmoothed && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: 10,
            color: "var(--text-tertiary)",
            marginTop: 4,
          }}
        >
          <svg width="16" height="2" aria-hidden="true">
            <line x1="0" y1="1" x2="16" y2="1" stroke="var(--text-secondary)" strokeWidth="1.5" strokeDasharray="4 3" />
          </svg>
          <span>{t("reports.daily.smoothed_label")}</span>
        </div>
      )}
      {hover != null && hover < days.length && (
        <div
          style={{
            position: "absolute",
            top: 8,
            left: 60,
            background: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 4,
            padding: "6px 10px",
            fontSize: 12,
            boxShadow: "0 2px 8px rgba(0,0,0,0.06)",
          }}
        >
          <div>
            <strong>{days[hover].date}</strong>:{" "}
            {t("reports.daily.tooltip_metrics", {
              min: (days[hover].avg_min ?? 0).toFixed(2),
              count: (days[hover].samples ?? 0).toLocaleString(),
            })}
          </div>
          {days[hover].avg_min_smoothed != null && (
            <div style={{ color: "var(--text-secondary)" }}>
              {t("reports.daily.smoothed_tooltip", { min: days[hover].avg_min_smoothed!.toFixed(2) })}
            </div>
          )}
          {days[hover].top_offenders?.length > 0 && (
            <div style={{ marginTop: 4, color: "var(--text-secondary)" }}>
              {t("reports.daily.worst_label")}{" "}
              {days[hover].top_offenders
                .slice(0, 3)
                .map((o) => t("reports.daily.offender", { code: o.route_code, min: o.avg_min.toFixed(1) }))
                .join(", ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
