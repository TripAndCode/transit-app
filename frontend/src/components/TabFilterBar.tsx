import type { CSSProperties } from "react";
import { useRangeContext, type DowFilter, type TimeBand } from "../api/rangeContext";

const DOW_OPTIONS: { value: DowFilter; label: string }[] = [
  { value: "all", label: "全曜日" },
  { value: "weekday", label: "平日" },
  { value: "weekend", label: "土日祝" },
];

const TIME_BAND_OPTIONS: { value: TimeBand; label: string }[] = [
  { value: "all", label: "全時間帯" },
  { value: "morning", label: "朝(05-09)" },
  { value: "forenoon", label: "午前(09-12)" },
  { value: "noon", label: "昼(12-14)" },
  { value: "afternoon", label: "午後(14-17)" },
  { value: "evening", label: "夕(17-20)" },
  { value: "night", label: "夜(20-24)" },
  { value: "late_night", label: "深夜(00-05)" },
];

const pill = (active: boolean): CSSProperties => ({
  background: active ? "var(--accent-soft)" : "var(--bg-surface)",
  color: active ? "var(--accent)" : "var(--text-secondary)",
  border: `1px solid ${active ? "var(--accent)" : "var(--border-subtle)"}`,
  borderRadius: 999,
  padding: "4px 12px",
  fontSize: 12,
  cursor: "pointer",
});

export function TabFilterBar() {
  const [ctx, setCtx] = useRangeContext();

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 12,
        alignItems: "center",
        padding: "8px 0 16px",
        borderBottom: "1px solid var(--border-soft)",
        marginBottom: 16,
      }}
    >
      <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>曜日</span>
      <div style={{ display: "flex", gap: 6 }}>
        {DOW_OPTIONS.map((o) => (
          <button
            key={o.value}
            type="button"
            onClick={() => setCtx({ dow: o.value })}
            style={pill(ctx.dow === o.value)}
          >
            {o.label}
          </button>
        ))}
      </div>
      <span style={{ fontSize: 12, color: "var(--text-tertiary)", marginLeft: 12 }}>時間帯</span>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {TIME_BAND_OPTIONS.map((o) => (
          <button
            key={o.value}
            type="button"
            onClick={() => setCtx({ time_band: o.value })}
            style={pill(ctx.time_band === o.value)}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}
