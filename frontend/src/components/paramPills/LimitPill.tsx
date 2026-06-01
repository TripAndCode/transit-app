/**
 * LimitPill — numeric stepper popover for selecting an integer result limit (k).
 *
 * Renders a pill button that opens an inline +/− stepper popover. A local draft
 * string buffers the raw input value so transient states (empty field, partially
 * typed digits) never propagate to the parent; the committed value is only written
 * on blur or Enter, after clamping to [min, max]. The draft re-syncs from `value`
 * whenever the parent updates it externally (e.g., chip-swap resetting defaults).
 */
import { useState, useRef, useEffect } from "react";

/** Props for {@link LimitPill}. */
export type LimitPillProps = {
  label: string;
  value: number;
  min?: number;
  max?: number;
  onChange: (next: number) => void;
  disabled?: boolean;
};

/** Numeric stepper pill that commits only on blur or Enter, guarding invalid drafts. */
export function LimitPill({ label, value, min = 3, max = 20, onChange, disabled }: LimitPillProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string>(String(value));
  useEffect(() => {
    setDraft(String(value));
  }, [value]);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  function close() {
    setOpen(false);
    triggerRef.current?.focus();
  }

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  /** Clamp and emit a numeric value; no-op for non-finite inputs. */
  function commit(next: number) {
    if (!Number.isFinite(next)) return;
    const clamped = Math.max(min, Math.min(max, Math.round(next)));
    onChange(clamped);
  }

  /** Attempt to commit the current draft string; reset draft to last good value if invalid or empty. */
  function commitDraft() {
    const trimmed = draft.trim();
    if (trimmed === "") {
      setDraft(String(value));
      return;
    }
    const parsed = Number(trimmed);
    if (Number.isFinite(parsed)) commit(parsed);
    else setDraft(String(value));
  }

  return (
    <div ref={ref} style={{ position: "relative", display: "inline-block" }}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => !disabled && setOpen((v) => !v)}
        disabled={disabled}
        onMouseEnter={(e) => {
          if (disabled) return;
          (e.currentTarget as HTMLButtonElement).style.background = "rgba(0,0,0,0.07)";
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLButtonElement).style.background = "var(--bg-soft, rgba(0,0,0,0.04))";
        }}
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
          transition: "background 120ms ease",
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
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commitDraft}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitDraft();
            }}
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
