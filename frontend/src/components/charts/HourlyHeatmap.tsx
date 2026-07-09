import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useRangeContext, type TimeBand } from "../../api/rangeContext";
import { DELAY_RAMP, delayColor } from "../../styles/tokens";

export type HourlyCell = {
  date: string;
  hour: number;
  avg_min: number | null;
  samples: number;
};

type Props = { cells: HourlyCell[]; height?: number };

// Hour ranges that map a clicked row to a time-band filter value.
const HOUR_TO_BAND: { hours: [number, number]; band: TimeBand }[] = [
  { hours: [0, 4], band: "late_night" },
  { hours: [5, 8], band: "morning" },
  { hours: [9, 11], band: "forenoon" },
  { hours: [12, 13], band: "noon" },
  { hours: [14, 16], band: "afternoon" },
  { hours: [17, 19], band: "evening" },
  { hours: [20, 23], band: "night" },
];

function bandFor(hour: number): TimeBand | null {
  for (const b of HOUR_TO_BAND) {
    if (hour >= b.hours[0] && hour <= b.hours[1]) return b.band;
  }
  return null;
}

/**
 * Date × hour-of-day heatmap. Rows = hours 0-23, columns = dates.
 * Color = delay severity (delayColor); empty cells dimmed. Hover = tooltip
 * with date/hour/avg/samples. Useful for spotting which times of day
 * delays cluster (rush hour, evening, etc.).
 */
export function HourlyHeatmap({ cells, height = 280 }: Props) {
  const { t } = useTranslation();
  const [hover, setHover] = useState<HourlyCell | null>(null);
  const [showLegend, setShowLegend] = useState(false);
  const [, setCtx] = useRangeContext();

  const dates = useMemo(() => {
    const s = new Set(cells.map((c) => c.date));
    return Array.from(s).sort();
  }, [cells]);

  const map = useMemo(() => {
    const m = new Map<string, HourlyCell>();
    for (const c of cells) m.set(`${c.date}|${c.hour}`, c);
    return m;
  }, [cells]);

  if (dates.length === 0) {
    return (
      <div style={{ padding: 24, color: "var(--text-tertiary)", textAlign: "center" }}>
        {t("reports.heatmap.empty")}
      </div>
    );
  }

  const padL = 38;
  const padT = 12;
  const padB = 28;
  const innerH = height - padT - padB;
  const cellH = innerH / 24;
  const innerW = Math.max(360, dates.length * 14);
  const cellW = innerW / dates.length;

  return (
    <div style={{ position: "relative", width: "100%", marginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 13 }}>{t("reports.heatmap.title")}</strong>
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          {t("reports.heatmap.subtitle")}
        </span>
        <button
          type="button"
          onClick={() => setShowLegend((v) => !v)}
          aria-label={t("reports.heatmap.legend_aria")}
          style={{
            background: "transparent",
            border: "1px solid var(--border-subtle)",
            borderRadius: "50%",
            width: 18,
            height: 18,
            fontSize: 11,
            color: "var(--text-secondary)",
            cursor: "pointer",
            padding: 0,
            lineHeight: 1,
          }}
        >
          ?
        </button>
        {showLegend && (
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              fontSize: 11,
              color: "var(--text-secondary)",
              padding: "4px 10px",
              background: "var(--bg-soft)",
              borderRadius: 4,
            }}
          >
            <span>{t("reports.heatmap.delay_label")}</span>
            <Swatch color={DELAY_RAMP.ok} label={t("reports.heatmap.lt_2")} />
            <Swatch color={DELAY_RAMP.mild} label="2-5" />
            <Swatch color={DELAY_RAMP.moderate} label="5-10" />
            <Swatch color={DELAY_RAMP.severe} label=">10" />
            <span style={{ color: "var(--text-tertiary)", marginLeft: 4 }}>
              {t("reports.heatmap.legend_explainer")}
            </span>
          </div>
        )}
      </div>
      <div style={{ overflowX: "auto" }}>
      <svg width={padL + innerW + 8} height={height} role="img" aria-label={t("reports.heatmap.svg_aria")}>
        {Array.from({ length: 24 }, (_, h) => (
          <text
            key={`h-${h}`}
            x={padL - 6}
            y={padT + h * cellH + cellH / 2 + 4}
            fontSize="10"
            fill="var(--text-tertiary)"
            textAnchor="end"
            style={{ cursor: bandFor(h) ? "pointer" : "default" }}
            onClick={() => {
              const b = bandFor(h);
              if (b) setCtx({ time_band: b });
            }}
          >
            {h}
          </text>
        ))}
        {dates.map((d, i) => {
          const stride = Math.max(1, Math.floor(dates.length / 8));
          if (i % stride !== 0 && i !== dates.length - 1) return null;
          return (
            <text
              key={`d-${d}`}
              x={padL + i * cellW + cellW / 2}
              y={height - 8}
              fontSize="10"
              fill="var(--text-tertiary)"
              textAnchor="middle"
              style={{ cursor: "pointer" }}
              onClick={() => setCtx({ from: d, to: d })}
            >
              {d.slice(5)}
            </text>
          );
        })}
        {dates.flatMap((d, i) =>
          Array.from({ length: 24 }, (_, h) => {
            const c = map.get(`${d}|${h}`);
            const x = padL + i * cellW;
            const y = padT + h * cellH;
            const fill = c && c.avg_min != null ? delayColor(c.avg_min) : "var(--bg-soft)";
            const opacity = c && c.avg_min != null ? Math.min(1, 0.35 + (c.samples / 200) * 0.5) : 0.35;
            const handleCellClick = () => {
              if (!c) return;
              const b = bandFor(c.hour);
              setCtx({ from: c.date, to: c.date, time_band: b ?? "all" });
            };
            return (
              <rect
                key={`${d}|${h}`}
                x={x + 0.5}
                y={y + 0.5}
                width={Math.max(1, cellW - 1)}
                height={Math.max(1, cellH - 1)}
                opacity={opacity}
                // `fill` goes in `style`, not the SVG presentation attribute:
                // delayColor()'s severe tier is now the literal "var(--delay-severe)",
                // and var() only resolves in a CSS property, not a presentation attr.
                style={{ fill, cursor: c ? "pointer" : "default" }}
                onMouseEnter={() => c && setHover(c)}
                onMouseLeave={() => setHover((v) => (v === c ? null : v))}
                onClick={handleCellClick}
              />
            );
          }),
        )}
      </svg>
      </div>
      {hover && (
        <div
          style={{
            position: "absolute",
            top: 32,
            right: 8,
            background: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 4,
            padding: "6px 10px",
            fontSize: 12,
            boxShadow: "0 2px 8px rgba(0,0,0,0.06)",
            pointerEvents: "none",
          }}
        >
          {hover.date} {t("reports.heatmap.tooltip_hour", { hour: String(hover.hour).padStart(2, "0") })}
          {" "}
          {t("reports.heatmap.tooltip_metrics", { min: (hover.avg_min ?? 0).toFixed(2), count: hover.samples })}
        </div>
      )}
    </div>
  );
}

function Swatch({ color, label }: { color: string; label: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
      <span style={{ width: 10, height: 10, background: color, borderRadius: 2 }} />
      {label}
    </span>
  );
}
