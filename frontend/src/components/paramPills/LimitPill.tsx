import { useState, useRef, useEffect } from "react";

export type LimitPillProps = {
  label: string;
  value: number;
  min?: number;
  max?: number;
  onChange: (next: number) => void;
  disabled?: boolean;
};

export function LimitPill({ label, value, min = 3, max = 20, onChange, disabled }: LimitPillProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  function commit(next: number) {
    if (!Number.isFinite(next)) return;
    const clamped = Math.max(min, Math.min(max, Math.round(next)));
    onChange(clamped);
  }

  return (
    <div ref={ref} style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        onClick={() => !disabled && setOpen((v) => !v)}
        disabled={disabled}
        style={{
          background: "var(--bg-soft, rgba(0,0,0,0.04))",
          border: "1px solid var(--border-soft, rgba(0,0,0,0.08))",
          borderRadius: 6,
          padding: "3px 8px",
          fontSize: 12,
          color: "var(--text-primary, #1a1a1a)",
          cursor: disabled ? "not-allowed" : "pointer",
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
        }}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <span style={{ color: "var(--text-secondary, #666)" }}>{label}:</span>
        <b>{value}</b>
        <span style={{ color: "var(--text-tertiary, #999)", fontSize: 10 }}>▾</span>
      </button>
      {open && (
        <div
          role="dialog"
          style={{
            position: "absolute",
            bottom: "calc(100% + 4px)",
            left: 0,
            background: "var(--bg-surface, white)",
            border: "1px solid var(--border-soft, rgba(0,0,0,0.12))",
            borderRadius: 8,
            padding: 8,
            boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
            zIndex: 10,
            display: "flex",
            gap: 6,
            alignItems: "center",
          }}
        >
          <button
            type="button"
            onClick={() => commit(value - 1)}
            disabled={value <= min}
            style={{
              width: 26,
              height: 26,
              borderRadius: 4,
              border: "1px solid var(--border-soft, rgba(0,0,0,0.08))",
              background: "var(--bg-soft, rgba(0,0,0,0.04))",
              cursor: value <= min ? "not-allowed" : "pointer",
              fontSize: 14,
            }}
            aria-label="decrement"
          >
            −
          </button>
          <input
            type="number"
            value={value}
            onChange={(e) => commit(Number(e.target.value))}
            min={min}
            max={max}
            style={{
              width: 56,
              padding: "3px 6px",
              fontSize: 13,
              textAlign: "center",
              border: "1px solid var(--border-soft, rgba(0,0,0,0.08))",
              borderRadius: 4,
              background: "var(--bg-surface, white)",
            }}
            aria-label={label}
          />
          <button
            type="button"
            onClick={() => commit(value + 1)}
            disabled={value >= max}
            style={{
              width: 26,
              height: 26,
              borderRadius: 4,
              border: "1px solid var(--border-soft, rgba(0,0,0,0.08))",
              background: "var(--bg-soft, rgba(0,0,0,0.04))",
              cursor: value >= max ? "not-allowed" : "pointer",
              fontSize: 14,
            }}
            aria-label="increment"
          >
            +
          </button>
        </div>
      )}
    </div>
  );
}
