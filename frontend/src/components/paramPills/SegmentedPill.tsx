import { useState, useRef, useEffect } from "react";

export type SegmentedOption = { value: string; label: string };

export type SegmentedPillProps = {
  label: string;
  value: string;
  options: SegmentedOption[];
  onChange: (next: string) => void;
  disabled?: boolean;
};

export function SegmentedPill({ label, value, options, onChange, disabled }: SegmentedPillProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const current = options.find((o) => o.value === value) ?? options[0];

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

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
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span style={{ color: "var(--text-secondary, #666)" }}>{label}:</span>
        <b>{current.label}</b>
        <span style={{ color: "var(--text-tertiary, #999)", fontSize: 10 }}>▾</span>
      </button>
      {open && (
        <div
          role="listbox"
          style={{
            position: "absolute",
            bottom: "calc(100% + 4px)",
            left: 0,
            background: "var(--bg-surface, white)",
            border: "1px solid var(--border-soft, rgba(0,0,0,0.12))",
            borderRadius: 8,
            padding: 4,
            boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
            zIndex: 10,
            minWidth: 120,
          }}
        >
          {options.map((opt) => (
            <button
              key={opt.value}
              type="button"
              role="option"
              aria-selected={opt.value === value}
              onClick={() => {
                onChange(opt.value);
                setOpen(false);
              }}
              style={{
                display: "block",
                width: "100%",
                background: opt.value === value ? "var(--accent-soft, rgba(74,138,170,0.12))" : "transparent",
                color: opt.value === value ? "var(--accent, #4a8aaa)" : "var(--text-primary, #1a1a1a)",
                border: "none",
                borderRadius: 4,
                padding: "5px 10px",
                fontSize: 12,
                textAlign: "left",
                cursor: "pointer",
              }}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
