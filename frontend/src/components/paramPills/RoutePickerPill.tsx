/**
 * RoutePickerPill — searchable route selector pill popover.
 *
 * Renders a pill button showing the selected route's line name — short_name,
 * falling back to long_name then the route code (or a placeholder when unset).
 * Clicking opens a listbox popover with a live-search text input filtering
 * routes by code, short name, or long name; each option shows the line name
 * with the code as a muted sub-label. Results are capped at 50 to keep
 * rendering fast. Closes on outside click, Escape, or option selection.
 */
import { useState, useRef, useEffect, useMemo } from "react";
import { useRoutes } from "../../api/hooks";

/** Props for {@link RoutePickerPill}. */
type RoutePickerPillProps = {
  label: string;
  value: string | null;
  agencyId: number;
  placeholder: string;
  onChange: (next: string) => void;
  disabled?: boolean;
};

/** Searchable route picker pill that filters routes by code/name and emits the selected route code. */
export function RoutePickerPill({
  label,
  value,
  agencyId,
  placeholder,
  onChange,
  disabled,
}: RoutePickerPillProps) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const { data: routes = [], isLoading } = useRoutes(agencyId);

  function close() {
    setQ("");
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

  const filtered = useMemo(() => {
    const ql = q.trim().toLowerCase();
    const list = (ql
      ? routes.filter(
          (r) =>
            (r.route_code ?? "").toLowerCase().includes(ql) ||
            (r.route_long_name ?? "").toLowerCase().includes(ql) ||
            (r.route_short_name ?? "").toLowerCase().includes(ql),
        )
      : routes
    ).filter((r) => r.route_code != null);
    return list.slice(0, 50);
  }, [routes, q]);

  // Prefer a human name (short_name is the line name, e.g. "L21 中央大橋線") over
  // the internal route_code; fall through to the code only as a last resort.
  const selected = value ? routes.find((r) => r.route_code === value) : undefined;
  const display = value
    ? selected?.route_short_name || selected?.route_long_name || value
    : placeholder;

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
          color: value ? "var(--text-primary, #1a1a1a)" : "var(--text-tertiary, #999)",
          cursor: disabled ? "not-allowed" : "pointer",
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          maxWidth: 200,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          transition: "background 120ms ease",
        }}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span style={{ color: "var(--text-secondary, #666)" }}>{label}:</span>
        <b style={{ overflow: "hidden", textOverflow: "ellipsis", maxWidth: 140 }}>{display}</b>
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
            padding: 6,
            boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
            zIndex: 10,
            width: 280,
          }}
        >
          <input
            type="text"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={placeholder}
            style={{
              width: "100%",
              padding: "4px 8px",
              fontSize: 12,
              border: "1px solid var(--border-soft, rgba(0,0,0,0.08))",
              borderRadius: 4,
              background: "var(--bg-surface, white)",
              marginBottom: 6,
            }}
            // eslint-disable-next-line jsx-a11y/no-autofocus -- search field of a just-opened route picker dropdown; focusing it is the expected UX
            autoFocus
          />
          <div style={{ maxHeight: 220, overflowY: "auto" }}>
            {isLoading && (
              <div style={{ padding: 6, fontSize: 12, color: "var(--text-tertiary, #999)" }}>
                …
              </div>
            )}
            {!isLoading && filtered.length === 0 && (
              <div style={{ padding: 6, fontSize: 12, color: "var(--text-tertiary, #999)" }}>
                —
              </div>
            )}
            {filtered.map((r) => {
              const code = r.route_code;
              return (
                <button
                  key={code ?? `_null_${r.route_id}`}
                  type="button"
                  role="option"
                  aria-selected={code === value}
                  onClick={() => {
                    if (code != null) {
                      onChange(code);
                    }
                    close();
                  }}
                  style={{
                    display: "block",
                    width: "100%",
                    background: code === value ? "var(--accent-soft, rgba(74,138,170,0.12))" : "transparent",
                    color: code === value ? "var(--accent, #5b6cad)" : "var(--text-primary, #1a1a1a)",
                    border: "none",
                    borderRadius: 4,
                    padding: "5px 8px",
                    fontSize: 12,
                    textAlign: "left",
                    cursor: "pointer",
                  }}
                >
                  {(() => {
                    const label = r.route_short_name || r.route_long_name || code;
                    return (
                      <>
                        <span>{label}</span>
                        {code && label !== code && (
                          <span style={{ color: "var(--text-tertiary, #999)" }}> · {code}</span>
                        )}
                      </>
                    );
                  })()}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
