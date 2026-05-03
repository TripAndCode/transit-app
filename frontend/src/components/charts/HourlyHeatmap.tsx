import { useMemo, useState } from "react";
import { delayColor } from "../../styles/tokens";

export type HourlyCell = {
  date: string;
  hour: number;
  avg_min: number | null;
  samples: number;
};

type Props = { cells: HourlyCell[]; height?: number };

/**
 * Date × hour-of-day heatmap. Rows = hours 0-23, columns = dates.
 * Color = delay severity (delayColor); empty cells dimmed. Hover = tooltip
 * with date/hour/avg/samples. Useful for spotting which times of day
 * delays cluster (rush hour, evening, etc.).
 */
export function HourlyHeatmap({ cells, height = 280 }: Props) {
  const [hover, setHover] = useState<HourlyCell | null>(null);

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
        時間帯別データがありません。
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
    <div style={{ position: "relative", width: "100%", overflowX: "auto", marginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <strong style={{ fontSize: 13 }}>時間帯ヒートマップ</strong>
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          縦: 時間 (0-23) ・ 横: 日付 ・ 色: 遅延の強さ
        </span>
      </div>
      <svg width={padL + innerW + 8} height={height} role="img" aria-label="時間帯別遅延ヒートマップ">
        {Array.from({ length: 24 }, (_, h) => (
          <text
            key={`h-${h}`}
            x={padL - 6}
            y={padT + h * cellH + cellH / 2 + 4}
            fontSize="10"
            fill="var(--text-tertiary)"
            textAnchor="end"
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
            const fill = c && c.avg_min != null ? delayColor(c.avg_min) : "#f0f0ee";
            const opacity = c && c.avg_min != null ? Math.min(1, 0.35 + (c.samples / 200) * 0.5) : 0.35;
            return (
              <rect
                key={`${d}|${h}`}
                x={x + 0.5}
                y={y + 0.5}
                width={Math.max(1, cellW - 1)}
                height={Math.max(1, cellH - 1)}
                fill={fill}
                opacity={opacity}
                onMouseEnter={() => c && setHover(c)}
                onMouseLeave={() => setHover((v) => (v === c ? null : v))}
              />
            );
          }),
        )}
      </svg>
      {hover && (
        <div
          style={{
            position: "absolute",
            top: 4,
            right: 8,
            background: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 4,
            padding: "6px 10px",
            fontSize: 12,
            boxShadow: "0 2px 8px rgba(0,0,0,0.06)",
          }}
        >
          {hover.date} {String(hover.hour).padStart(2, "0")}時 ・ 平均
          {(hover.avg_min ?? 0).toFixed(2)}分 ・ {hover.samples}件
        </div>
      )}
    </div>
  );
}
