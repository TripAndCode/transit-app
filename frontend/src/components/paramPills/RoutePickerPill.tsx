/**
 * RoutePickerPill — searchable route selector.
 *
 * A field-like trigger (search glyph + selected route name, or a search-prompt
 * placeholder when unset) opens a downward popover: a live-search input filtering
 * routes by code / short name / long name, then a list where each option shows the
 * line name with the code as a muted monospace sub-label. When `delays` is supplied
 * (ForecastTab passes per-route avg delay), each row also shows a warm-ramp delay
 * chip. Results are capped at 50. Closes on outside click, Escape, or selection.
 */
import { useState, useRef, useEffect, useMemo } from "react";
import { useRoutes } from "../../api/hooks";
import { delayColor } from "../../styles/tokens";
import "./RoutePickerPill.css";

const SearchIcon = ({ size = 14 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <circle cx="11" cy="11" r="7" />
    <line x1="21" y1="21" x2="16.5" y2="16.5" />
  </svg>
);

/** Props for {@link RoutePickerPill}. */
type RoutePickerPillProps = {
  label: string;
  value: string | null;
  agencyId: number;
  placeholder: string;
  onChange: (next: string) => void;
  disabled?: boolean;
  /** Optional per-route avg delay (minutes), keyed by route_code; shows a warm-ramp chip. */
  delays?: Record<string, number | null | undefined>;
};

/** Searchable route picker that filters routes by code/name and emits the selected route code. */
export function RoutePickerPill({
  label,
  value,
  agencyId,
  placeholder,
  onChange,
  disabled,
  delays,
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
  const display = value ? selected?.route_short_name || selected?.route_long_name || value : placeholder;

  return (
    <div ref={ref} className="rp">
      <button
        ref={triggerRef}
        type="button"
        className={`rp-trigger${value ? "" : " is-empty"}${open ? " is-open" : ""}`}
        onClick={() => !disabled && setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label}
      >
        <span className="rp-glyph" aria-hidden>
          <SearchIcon />
        </span>
        <span className="rp-value">{display}</span>
        <span className="rp-caret" aria-hidden>
          ▾
        </span>
      </button>
      {open && (
        <div role="listbox" className="rp-pop">
          <div className="rp-search-wrap">
            <span className="rp-search-icon" aria-hidden>
              <SearchIcon />
            </span>
            <input
              type="text"
              className="rp-search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={placeholder}
              // eslint-disable-next-line jsx-a11y/no-autofocus -- search field of a just-opened route picker dropdown; focusing it is the expected UX
              autoFocus
            />
          </div>
          <div className="rp-list">
            {isLoading && <div className="rp-msg">…</div>}
            {!isLoading && filtered.length === 0 && <div className="rp-msg">—</div>}
            {filtered.map((r) => {
              const code = r.route_code;
              const name = r.route_short_name || r.route_long_name || code;
              const delay = code != null ? delays?.[code] : null;
              return (
                <button
                  key={code ?? `_null_${r.route_id}`}
                  type="button"
                  role="option"
                  className="rp-opt"
                  aria-selected={code === value}
                  onClick={() => {
                    if (code != null) onChange(code);
                    close();
                  }}
                >
                  <span className="rp-opt-name">{name}</span>
                  {code && name !== code ? <span className="rp-opt-code">{code}</span> : <span />}
                  {delay != null ? (
                    <span className="rp-opt-delay">
                      <span className="rp-delay-dot" style={{ background: delayColor(delay) }} />
                      <span className="rp-delay-val">{delay.toFixed(1)}</span>
                    </span>
                  ) : (
                    <span />
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
