import { useEffect, useRef, useState } from "react";
import { isoDaysAgo, todayISO, useRangeContext, type RangeCtx } from "../api/rangeContext";

type Preset = { label: string; from: () => string; to: () => string };

const PRESETS: Preset[] = [
  { label: "直近7日", from: () => isoDaysAgo(6), to: todayISO },
  { label: "直近30日", from: () => isoDaysAgo(29), to: todayISO },
  { label: "直近90日", from: () => isoDaysAgo(89), to: todayISO },
  { label: "今月", from: () => firstOfMonth(0), to: () => lastOfMonth(0) },
  { label: "先月", from: () => firstOfMonth(-1), to: () => lastOfMonth(-1) },
];

function firstOfMonth(offset: number): string {
  const d = new Date();
  d.setUTCDate(1);
  d.setUTCMonth(d.getUTCMonth() + offset);
  return d.toISOString().slice(0, 10);
}

function lastOfMonth(offset: number): string {
  const d = new Date();
  d.setUTCDate(1);
  d.setUTCMonth(d.getUTCMonth() + offset + 1);
  d.setUTCDate(0);
  return d.toISOString().slice(0, 10);
}

function presetLabel(ctx: RangeCtx): string {
  for (const p of PRESETS) {
    if (ctx.from === p.from() && ctx.to === p.to()) return p.label;
  }
  return `${ctx.from} 〜 ${ctx.to}`;
}

export function RangeBadge() {
  const [ctx, setCtx] = useRangeContext();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  function applyPreset(p: Preset) {
    setCtx({ from: p.from(), to: p.to() });
    setOpen(false);
  }

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          background: "var(--bg-surface)",
          border: "1px solid var(--border-subtle)",
          borderRadius: "var(--radius)",
          padding: "6px 12px",
          fontSize: 13,
          minWidth: 160,
          textAlign: "left",
        }}
      >
        📅 {presetLabel(ctx)} <span style={{ float: "right", color: "var(--text-tertiary)" }}>▾</span>
      </button>
      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            right: 0,
            minWidth: 260,
            background: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius)",
            boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
            zIndex: 20,
            padding: 8,
          }}
        >
          {PRESETS.map((p) => (
            <div
              key={p.label}
              onClick={() => applyPreset(p)}
              style={{
                padding: "8px 12px",
                cursor: "pointer",
                borderRadius: 4,
                fontSize: 13,
              }}
            >
              {p.label}
            </div>
          ))}
          <div style={{ borderTop: "1px solid var(--border-soft)", margin: "6px 0" }} />
          <div style={{ padding: "0 8px", fontSize: 12, color: "var(--text-secondary)", marginBottom: 4 }}>
            カスタム
          </div>
          <div style={{ display: "flex", gap: 6, padding: "0 8px 8px" }}>
            <input
              type="date"
              value={ctx.from}
              max={ctx.to}
              onChange={(e) => setCtx({ from: e.target.value })}
              style={{ flex: 1, fontSize: 13 }}
            />
            <span style={{ alignSelf: "center", color: "var(--text-tertiary)" }}>〜</span>
            <input
              type="date"
              value={ctx.to}
              min={ctx.from}
              onChange={(e) => setCtx({ to: e.target.value })}
              style={{ flex: 1, fontSize: 13 }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
