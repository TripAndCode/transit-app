import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { delayColor } from "../../styles/tokens";
import type { TrendDay } from "../../api/types";

type Props = { days: TrendDay[]; height?: number };

export function DailyChart({ days, height = 240 }: Props) {
  const { t } = useTranslation();
  const [hover, setHover] = useState<number | null>(null);

  // If the data shrinks (filter narrowed), drop a stale hover index so the
  // tooltip doesn't dereference out-of-bounds.
  useEffect(() => {
    if (hover != null && hover >= days.length) setHover(null);
  }, [days.length, hover]);
  const W = 760;
  const H = height;
  const padL = 44;
  const padR = 12;
  const padT = 12;
  const padB = 28;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  const stats = useMemo(() => {
    const avgs = days.map((d) => d.avg_min ?? 0);
    const samples = days.map((d) => d.samples ?? 0);
    return {
      maxAvg: Math.max(1, ...avgs),
      maxSamples: Math.max(1, ...samples),
    };
  }, [days]);

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
              <circle cx={x} cy={y} r={hover === i ? 5 : 3} fill={c} stroke="#fff" strokeWidth="1.5" />
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
