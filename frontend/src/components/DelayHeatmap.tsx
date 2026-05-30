// frontend/src/components/DelayHeatmap.tsx
import { useTranslation } from "react-i18next";

import type { HeatmapResponse } from "../api/types";
import { Skeleton } from "./Skeleton";

// ─── Public types ────────────────────────────────────────────────────────────

export type DelayHeatmapProps = {
  data: HeatmapResponse | undefined;
  isLoading: boolean;
  isError: boolean;
  /** Called when user clicks a cell. Provides route_code + dimension label and value. */
  onCellClick?: (
    route_code: string,
    dimension: string,
    value: number | null
  ) => void;
  /** Toggle between "dow" and "hour_band". Parent owns state. */
  dimension: "dow" | "hour_band";
  onDimensionChange: (d: "dow" | "hour_band") => void;
};

// ─── Color scale helpers ──────────────────────────────────────────────────────

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

type CellStyle = {
  background: string;
  color: string;
};

function cellStyle(value: number | null, baseline: number): CellStyle {
  if (value === null) {
    return {
      background: "var(--bg-soft, rgba(0,0,0,0.03))",
      color: "var(--text-secondary, #888)",
    };
  }

  if (value < baseline - 0.5) {
    // Better than network — blue tint (calm, never alarming)
    const lightness = 95 - clamp((baseline - value) * 8, 0, 25);
    return {
      background: `hsl(210, 40%, ${lightness}%)`,
      color: "#1a2a38",
    };
  }

  if (Math.abs(value - baseline) <= 0.5) {
    // Within ±0.5 of baseline — neutral warm-white
    return {
      background: "hsl(45, 30%, 96%)",
      color: "#3a3322",
    };
  }

  // Worse than network — warm (orange/amber, saturation ≤ 50%, NEVER pure red)
  const lightness = 94 - clamp((value - baseline) * 6, 0, 28);
  const textColor = lightness > 70 ? "#3a2a10" : "#ffffff";
  return {
    background: `hsl(25, 50%, ${lightness}%)`,
    color: textColor,
  };
}

// ─── Dimension label helper ───────────────────────────────────────────────────

function formatDimension(
  dim: string,
  dimensionType: "dow" | "hour_band",
  t: (key: string) => string
): string {
  if (dimensionType === "dow") {
    const key = `ask.dashboard.heatmap.dow.${dim.toLowerCase()}`;
    const translated = t(key);
    // If translation returns the key itself (missing), fall back to original
    return translated === key ? dim : translated;
  }
  // hour_band: show as-is (e.g. "07-09")
  return dim;
}

// ─── Component ───────────────────────────────────────────────────────────────

export function DelayHeatmap({
  data,
  isLoading,
  isError,
  onCellClick,
  dimension,
  onDimensionChange,
}: DelayHeatmapProps): JSX.Element {
  const { t } = useTranslation();

  const titleKey =
    dimension === "dow"
      ? "ask.dashboard.heatmap.title_dow"
      : "ask.dashboard.heatmap.title_hour_band";

  // ── Header row ──────────────────────────────────────────────────────────────
  function renderHeader(dims: string[]) {
    return (
      <div role="row" style={{ display: "contents" }}>
        {/* Route label column header (empty spacer) */}
        <div
          role="columnheader"
          aria-label="route"
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: "var(--text-secondary, #888)",
            padding: "0 4px 4px 0",
            overflow: "hidden",
            whiteSpace: "nowrap",
          }}
        />
        {dims.map((d) => (
          <div
            key={d}
            role="columnheader"
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: "var(--text-secondary, #888)",
              textAlign: "center",
              padding: "0 2px 4px",
              overflow: "hidden",
              whiteSpace: "nowrap",
            }}
          >
            {formatDimension(d, dimension, t)}
          </div>
        ))}
      </div>
    );
  }

  // ── Loading skeleton ─────────────────────────────────────────────────────────
  if (isLoading) {
    // Show 5 skeleton rows with a placeholder header
    const placeholderDims = Array.from({ length: 7 }, (_, i) => String(i));
    return (
      <section aria-busy="true" aria-label={t(titleKey)}>
        {renderTitleRow()}
        <div
          role="table"
          aria-label={t(titleKey)}
          style={{
            display: "grid",
            gridTemplateColumns: `120px repeat(${placeholderDims.length}, 1fr)`,
            gap: "2px",
          }}
        >
          {renderHeader(placeholderDims.map(() => "…"))}
          {Array.from({ length: 5 }).map((_, rowIdx) => (
            <div key={rowIdx} role="row" style={{ display: "contents" }}>
              <div style={{ padding: "1px 4px 1px 0" }}>
                <Skeleton width="100%" height={28} />
              </div>
              {placeholderDims.map((_, colIdx) => (
                <div key={colIdx} style={{ padding: "1px" }}>
                  <Skeleton width="100%" height={28} />
                </div>
              ))}
            </div>
          ))}
        </div>
      </section>
    );
  }

  // ── Error state ──────────────────────────────────────────────────────────────
  if (isError) {
    return (
      <section>
        {renderTitleRow()}
        <p
          style={{
            margin: "8px 0 0",
            fontSize: 13,
            color: "var(--text-secondary, #888)",
          }}
        >
          {t("ask.dashboard.heatmap.error")}
        </p>
      </section>
    );
  }

  // ── Empty state ───────────────────────────────────────────────────────────────
  if (!data || data.routes.length === 0) {
    return (
      <section>
        {renderTitleRow()}
        <p
          style={{
            margin: "8px 0 0",
            fontSize: 13,
            color: "var(--text-secondary, #888)",
          }}
        >
          {t("ask.dashboard.heatmap.empty")}
        </p>
      </section>
    );
  }

  // ── Full grid ────────────────────────────────────────────────────────────────
  const { routes, dimensions, cells, baseline_min } = data;

  return (
    <section>
      {renderTitleRow()}
      <div
        role="table"
        aria-label={t(titleKey)}
        style={{
          display: "grid",
          gridTemplateColumns: `120px repeat(${dimensions.length}, 1fr)`,
          gap: "2px",
          overflowX: "auto",
        }}
      >
        {renderHeader(dimensions)}

        {routes.map((route, rowIdx) => {
          const rowCells = cells[rowIdx] ?? [];
          return (
            <div key={route.route_code} role="row" style={{ display: "contents" }}>
              {/* Route label */}
              <div
                role="rowheader"
                title={route.label}
                style={{
                  fontSize: 12,
                  fontWeight: 500,
                  color: "var(--text-primary, #333)",
                  padding: "0 6px 0 0",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  height: 28,
                  display: "flex",
                  alignItems: "center",
                  direction: "rtl",
                  textAlign: "left",
                }}
              >
                <span style={{ direction: "ltr", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {route.label}
                </span>
              </div>

              {/* Data cells */}
              {dimensions.map((dim, colIdx) => {
                const value = rowCells[colIdx] ?? null;
                const style = cellStyle(value, baseline_min);
                const dimLabel = formatDimension(dim, dimension, t);
                const ariaLabel =
                  value === null
                    ? `${route.label}, ${dimLabel}: データなし`
                    : t("ask.dashboard.heatmap.cell_aria", {
                        route: route.label,
                        dim: dimLabel,
                        val: value.toFixed(1),
                      });

                const cellContent = value === null ? "-" : value.toFixed(1);

                const sharedCellStyle: React.CSSProperties = {
                  height: 28,
                  minWidth: 0,
                  padding: 0,
                  fontSize: 12,
                  fontWeight: 500,
                  textAlign: "center",
                  lineHeight: "28px",
                  background: style.background,
                  color: style.color,
                  border: "none",
                  borderRadius: 2,
                  cursor: onCellClick ? "pointer" : "default",
                  transition: "box-shadow 0.1s",
                  overflow: "hidden",
                  whiteSpace: "nowrap",
                  width: "100%",
                  display: "block",
                };

                if (onCellClick) {
                  return (
                    <div key={dim} role="cell" style={{ padding: 1 }}>
                      <button
                        aria-label={ariaLabel}
                        onClick={() => onCellClick(route.route_code, dim, value)}
                        style={sharedCellStyle}
                        onMouseEnter={(e) => {
                          (e.currentTarget as HTMLButtonElement).style.boxShadow =
                            "inset 0 0 0 2px var(--accent, #4a8aaa)";
                        }}
                        onMouseLeave={(e) => {
                          (e.currentTarget as HTMLButtonElement).style.boxShadow = "none";
                        }}
                      >
                        {cellContent}
                      </button>
                    </div>
                  );
                }

                return (
                  <div
                    key={dim}
                    role="cell"
                    aria-label={ariaLabel}
                    style={{ padding: 1 }}
                  >
                    <div style={sharedCellStyle}>{cellContent}</div>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </section>
  );

  // ── Title row (segmented dimension toggle) ────────────────────────────────────
  function renderTitleRow() {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 8,
          gap: 8,
        }}
      >
        <h3
          style={{
            margin: 0,
            fontSize: 13,
            fontWeight: 600,
            color: "var(--text-primary, #333)",
          }}
        >
          {t(titleKey)}
        </h3>

        {/* Segmented control */}
        <div
          role="tablist"
          aria-label={t("ask.dashboard.heatmap.title_dow")}
          style={{
            display: "flex",
            gap: 2,
            background: "var(--bg-soft, rgba(0,0,0,0.05))",
            borderRadius: 6,
            padding: 2,
          }}
        >
          {(["dow", "hour_band"] as const).map((d) => (
            <button
              key={d}
              role="tab"
              aria-pressed={dimension === d}
              aria-selected={dimension === d}
              onClick={() => onDimensionChange(d)}
              style={{
                padding: "2px 10px",
                fontSize: 12,
                fontWeight: dimension === d ? 600 : 400,
                border: "none",
                borderRadius: 4,
                cursor: "pointer",
                background:
                  dimension === d
                    ? "var(--bg-card, #fff)"
                    : "transparent",
                color:
                  dimension === d
                    ? "var(--text-primary, #333)"
                    : "var(--text-secondary, #888)",
                boxShadow:
                  dimension === d
                    ? "0 1px 3px rgba(0,0,0,0.12)"
                    : "none",
                transition: "all 0.15s",
              }}
            >
              {d === "dow"
                ? t("ask.dashboard.heatmap.toggle_dow")
                : t("ask.dashboard.heatmap.toggle_hour_band")}
            </button>
          ))}
        </div>
      </div>
    );
  }
}
