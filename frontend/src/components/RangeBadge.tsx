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

// JST-pinned month boundaries. See rangeContext.ts for why the
// frontend pins Asia/Tokyo and not UTC.
const _TZ = "Asia/Tokyo";
const _fmtParts = new Intl.DateTimeFormat("en-CA", {
  timeZone: _TZ,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

function _jstYearMonth(d: Date): { year: number; month: number } {
  // formatToParts returns the calendar year/month in the target TZ.
  const parts = _fmtParts.formatToParts(d);
  const year = Number(parts.find((p) => p.type === "year")!.value);
  const month = Number(parts.find((p) => p.type === "month")!.value);
  return { year, month };
}

function firstOfMonth(offset: number): string {
  const { year, month } = _jstYearMonth(new Date());
  // month is 1-12; offset is 0 for current month, -1 for last month, etc.
  // Anchor at 12:00 UTC to stay safe across all browser TZ contexts.
  const target = new Date(Date.UTC(year, month - 1 + offset, 1, 12));
  return _fmtParts.format(target);
}

function lastOfMonth(offset: number): string {
  const { year, month } = _jstYearMonth(new Date());
  // Day 0 of next month = last day of target month.
  // Anchor at 12:00 UTC to stay safe across all browser TZ contexts.
  const target = new Date(Date.UTC(year, month + offset, 0, 12));
  return _fmtParts.format(target);
}

function jpDate(iso: string): string {
  // yyyy-mm-dd → yyyy/mm/dd (Japan-conventional written form).
  return iso.replaceAll("-", "/");
}

function presetLabel(ctx: RangeCtx): string {
  for (const p of PRESETS) {
    if (ctx.from === p.from() && ctx.to === p.to()) return p.label;
  }
  return `${jpDate(ctx.from)} 〜 ${jpDate(ctx.to)}`;
}

function isDefault(ctx: RangeCtx): boolean {
  return ctx.from === isoDaysAgo(29) && ctx.to === todayISO();
}

export function RangeBadge() {
  const [ctx, setCtx] = useRangeContext();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const active = !isDefault(ctx);

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
          background: active ? "var(--accent)" : "var(--bg-surface)",
          color: active ? "#fff" : "var(--text-primary)",
          border: `1px solid ${active ? "var(--accent)" : "var(--border-subtle)"}`,
          borderRadius: 8,
          padding: "8px 16px",
          fontSize: 14,
          fontWeight: 600,
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          cursor: "pointer",
          boxShadow: active ? "0 1px 3px rgba(91,108,173,0.30)" : "none",
          transition: "all var(--transition)",
        }}
      >
        <span aria-hidden style={{ fontSize: 16 }}>📅</span>
        {presetLabel(ctx)}
        <span style={{ opacity: 0.7 }}>▾</span>
      </button>
      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            left: 0,
            zIndex: 50,
            minWidth: 280,
            background: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius-lg)",
            boxShadow: "0 8px 32px rgba(0,0,0,0.10)",
            padding: 8,
            color: "var(--text-primary)",
          }}
        >
          {PRESETS.map((p) => {
            const selected = ctx.from === p.from() && ctx.to === p.to();
            return (
              <div
                key={p.label}
                onClick={() => applyPreset(p)}
                style={{
                  padding: "8px 12px",
                  cursor: "pointer",
                  borderRadius: 4,
                  fontSize: 13,
                  background: selected ? "var(--accent-soft)" : "transparent",
                  color: selected ? "var(--accent)" : "var(--text-primary)",
                  fontWeight: selected ? 500 : 400,
                }}
              >
                {p.label}
              </div>
            );
          })}
          <div style={{ borderTop: "1px solid var(--border-soft)", margin: "6px 0" }} />
          <div style={{ padding: "0 8px", fontSize: 12, color: "var(--text-secondary)", marginBottom: 6 }}>
            カスタム
          </div>
          <div style={{ display: "flex", gap: 6, padding: "0 8px 8px", alignItems: "center" }}>
            <input
              type="date"
              lang="ja"
              value={ctx.from}
              max={ctx.to}
              onChange={(e) => setCtx({ from: e.target.value })}
              style={{ flex: 1, fontSize: 13, padding: "4px 6px" }}
            />
            <span style={{ color: "var(--text-tertiary)" }}>〜</span>
            <input
              type="date"
              lang="ja"
              value={ctx.to}
              min={ctx.from}
              onChange={(e) => setCtx({ to: e.target.value })}
              style={{ flex: 1, fontSize: 13, padding: "4px 6px" }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
