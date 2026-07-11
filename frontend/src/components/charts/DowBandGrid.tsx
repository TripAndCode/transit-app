import { delayColor } from "../../styles/tokens";
import { BAND_ORDER, type Band, type ForecastOverviewGridCell } from "../../api/types";

const RAMP_STOPS = 5;

/** Anchored min→max colour ramp legend (shown inline, not only in the modal).
 * `colorFor` defaults to the absolute ramp; pass a relative ramp to match a grid
 * that uses one, so the legend reflects the encoding actually shown. */
export function Legend({ min, max, unit, colorFor = delayColor }: { min: number; max: number; unit: string; colorFor?: (v: number) => string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 12, fontSize: 11, color: "var(--text-secondary)" }}>
      <span style={{ fontVariantNumeric: "tabular-nums" }}>{min.toFixed(1)}</span>
      <span style={{ display: "inline-flex", gap: 2 }}>
        {Array.from({ length: RAMP_STOPS }, (_, i) => (
          <span key={i} style={{ width: 14, height: 14, borderRadius: 2, background: colorFor(min + ((max - min) * i) / (RAMP_STOPS - 1)) }} />
        ))}
      </span>
      <span style={{ fontVariantNumeric: "tabular-nums" }}>{max.toFixed(1)}</span>
      <span style={{ color: "var(--text-tertiary)", marginLeft: 4 }}>{unit}</span>
    </div>
  );
}

/** 7-day × 5-band grid. Dense by construction — used for the agency overview and
 * the per-route detail (route cells collapsed to bands client-side). */
export function BandGrid({
  grid,
  bandLabel,
  dayLabel,
  axisMin,
  colorFor,
  onTip,
  onLeave,
}: {
  grid: ForecastOverviewGridCell[];
  bandLabel: (b: Band) => string;
  dayLabel: (dow: number) => string;
  axisMin: string;
  colorFor: (v: number) => string;
  onTip: (e: React.MouseEvent, text: string) => void;
  onLeave: () => void;
}) {
  const byKey = new Map(grid.map((c) => [`${c.dow}-${c.band}`, c]));
  const cols = `34px repeat(${BAND_ORDER.length}, 1fr)`;
  return (
    <div onMouseLeave={onLeave}>
      <div style={{ display: "grid", gridTemplateColumns: cols, gap: 4 }}>
        <span />
        {BAND_ORDER.map((b) => (
          <span key={b} style={{ fontSize: 11, color: "var(--text-tertiary)", textAlign: "center" }}>
            {bandLabel(b)}
          </span>
        ))}
        {Array.from({ length: 7 }, (_, di) => {
          const dow = di + 1;
          return [
            <div key={`l${dow}`} style={{ fontSize: 11, color: "var(--text-secondary)", textAlign: "right", paddingRight: 6, display: "flex", alignItems: "center", justifyContent: "flex-end" }}>
              {dayLabel(dow)}
            </div>,
            ...BAND_ORDER.map((b) => {
              const c = byKey.get(`${dow}-${b}`);
              const v = c?.expected_avg_min ?? null;
              const tipText = `${dayLabel(dow)} ${bandLabel(b)} · ${v == null ? "—" : `${v.toFixed(1)}${axisMin}`}`;
              if (v == null) {
                return (
                  <div
                    key={b}
                    data-testid="ov-band-cell"
                    onMouseEnter={(e) => onTip(e, tipText)}
                    onMouseMove={(e) => onTip(e, tipText)}
                    style={{ height: 30, borderRadius: 3, background: "repeating-linear-gradient(45deg,var(--border-soft),var(--border-soft) 3px,var(--bg-soft) 3px,var(--bg-soft) 6px)" }}
                  />
                );
              }
              return (
                <div
                  key={b}
                  data-testid="ov-band-cell"
                  onMouseEnter={(e) => onTip(e, tipText)}
                  onMouseMove={(e) => onTip(e, tipText)}
                  style={{ height: 30, borderRadius: 3, background: colorFor(v), opacity: c?.low_confidence ? 0.5 : 1 }}
                />
              );
            }),
          ];
        })}
      </div>
    </div>
  );
}
