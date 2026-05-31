// frontend/src/components/MoversList.tsx
import { useTranslation } from "react-i18next";

import type { MoversResponse } from "../api/types";
import { Skeleton } from "./Skeleton";

export type MoversListProps = {
  data: MoversResponse | undefined;
  isLoading: boolean;
  isError: boolean;
  /** Click a row → drill into that route's timeline. */
  onRowClick?: (route_code: string) => void;
  /** Window the parent passed (used in subtitle). Default 7. */
  windowDays?: number;
};

// Calm, non-alarm delta colors — saturation ≤ 55 % throughout
const COLOR_WORSE = "hsl(25, 55%, 40%)";      // warm brown-tan
const COLOR_BETTER = "hsl(160, 40%, 35%)";    // calm teal-green
const COLOR_NEUTRAL = "var(--text-secondary, #888)";

const HOVER_TINT_WORSE = "hsl(25, 40%, 96%)";
const HOVER_TINT_BETTER = "hsl(160, 30%, 96%)";
const HOVER_BG_SOFT = "var(--bg-soft, rgba(0,0,0,0.04))";
const BORDER = "1px solid var(--border-subtle, rgba(0,0,0,0.06))";

function deltaColor(delta: number): string {
  if (delta > 0.3) return COLOR_WORSE;
  if (delta < -0.3) return COLOR_BETTER;
  return COLOR_NEUTRAL;
}

function deltaTint(delta: number): string | undefined {
  if (delta > 0.3) return HOVER_TINT_WORSE;
  if (delta < -0.3) return HOVER_TINT_BETTER;
  return undefined;
}

function formatDelta(delta: number): string {
  const sign = delta > 0 ? "+" : delta < 0 ? "−" : "";
  return `${sign}${Math.abs(delta).toFixed(1)}分`;
}

function formatPct(pct: number): string {
  // Convert to percentage string with sign, e.g. +12% or −8%
  const abs = Math.abs(pct * 100);
  const sign = pct > 0 ? "+" : pct < 0 ? "−" : "";
  return `(${sign}${abs.toFixed(0)}%)`;
}

export function MoversList({
  data,
  isLoading,
  isError,
  onRowClick,
  windowDays = 7,
}: MoversListProps): JSX.Element {
  const { t } = useTranslation();

  // --- Loading ---
  if (isLoading) {
    return (
      <div style={{ padding: "8px 0" }}>
        <div style={{ padding: "0 12px 6px", fontSize: 13, fontWeight: 600 }}>
          {t("ask.dashboard.movers.title")}
        </div>
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} style={{ padding: "4px 12px" }}>
            <Skeleton width="100%" height={36} />
          </div>
        ))}
      </div>
    );
  }

  // --- Error ---
  if (isError) {
    return (
      <div style={{ padding: "8px 12px" }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
          {t("ask.dashboard.movers.title")}
        </div>
        <span style={{ fontSize: 12, color: "var(--text-secondary, #888)" }}>
          {t("ask.dashboard.movers.error")}
        </span>
      </div>
    );
  }

  const rows = data?.rows ?? [];

  return (
    <div>
      {/* Header */}
      <div style={{ padding: "8px 12px 4px" }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 2 }}>
          {t("ask.dashboard.movers.title")}
        </div>
        <div
          style={{
            fontSize: 11,
            color: "var(--text-secondary, #888)",
            lineHeight: 1.4,
          }}
        >
          {t("ask.dashboard.movers.subtitle", { days: windowDays })}
        </div>
      </div>

      {/* Empty state */}
      {rows.length === 0 && (
        <div
          style={{
            padding: "10px 12px",
            fontSize: 12,
            color: "var(--text-secondary, #888)",
          }}
        >
          {t("ask.dashboard.movers.empty")}
        </div>
      )}

      {/* Ranked list */}
      {rows.length > 0 && (
        <ol
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
          }}
        >
          {rows.map((row, idx) => {
            const rank = idx + 1;
            const color = deltaColor(row.delta);
            const tint = deltaTint(row.delta);
            const isClickable = !!onRowClick;
            const lowSamples = row.samples < 10;

            // Determine pct cell content
            let pctContent: React.ReactNode;
            if (row.current_avg === null && row.previous_avg !== null) {
              pctContent = (
                <span>{t("ask.dashboard.movers.stopped")}</span>
              );
            } else if (row.previous_avg === null && row.current_avg !== null) {
              pctContent = (
                <span>{t("ask.dashboard.movers.new")}</span>
              );
            } else if (row.delta_pct !== null) {
              pctContent = <span>{formatPct(row.delta_pct)}</span>;
            } else {
              pctContent = null;
            }

            // Build aria-label
            const deltaSignedAbs = `${row.delta >= 0 ? "+" : "−"}${Math.abs(row.delta).toFixed(1)}`;
            const pctStr =
              row.delta_pct !== null
                ? `${row.delta_pct >= 0 ? "+" : "−"}${Math.abs(row.delta_pct * 100).toFixed(0)}%`
                : row.current_avg === null
                ? t("ask.dashboard.movers.stopped")
                : t("ask.dashboard.movers.new");
            const ariaLabel = t("ask.dashboard.movers.row_aria", {
              rank,
              label: row.label,
              delta: deltaSignedAbs,
              pct: pctStr,
              samples: row.samples,
            });

            return (
              <li
                key={row.route_code}
                style={{
                  borderTop: idx > 0 ? BORDER : undefined,
                }}
              >
                <RowButton
                  ariaLabel={ariaLabel}
                  isClickable={isClickable}
                  tint={tint}
                  onRowClick={onRowClick}
                  routeCode={row.route_code}
                >
                  {/* Col 1: rank */}
                  <span
                    style={{
                      fontSize: 11,
                      color: "var(--text-secondary, #888)",
                      textAlign: "right",
                      lineHeight: 1,
                    }}
                  >
                    {rank}.
                  </span>

                  {/* Col 2: route label */}
                  <span
                    style={{
                      fontSize: 13,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={row.label}
                  >
                    {row.label}
                  </span>

                  {/* Col 3: delta value */}
                  <span
                    style={{
                      fontSize: 12,
                      fontWeight: 600,
                      color,
                      whiteSpace: "nowrap",
                    }}
                  >
                    {formatDelta(row.delta)}
                  </span>

                  {/* Col 4: pct (+ low-samples warning) */}
                  <span
                    style={{
                      fontSize: 11,
                      color: "var(--text-secondary, #888)",
                      display: lowSamples ? "flex" : "inline",
                      flexDirection: lowSamples ? "column" : undefined,
                      alignItems: lowSamples ? "flex-end" : undefined,
                      whiteSpace: "nowrap",
                      lineHeight: 1.3,
                    }}
                  >
                    {pctContent}
                    {lowSamples && (
                      <span style={{ fontSize: 10, opacity: 0.75 }}>
                        {t("ask.dashboard.movers.low_samples")}
                      </span>
                    )}
                  </span>
                </RowButton>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

// Extracted to keep hover logic clean with onMouseEnter/Leave style mutation
type RowButtonProps = {
  ariaLabel: string;
  isClickable: boolean;
  tint: string | undefined;
  onRowClick?: (route_code: string) => void;
  routeCode: string;
  children: React.ReactNode;
};

function RowButton({
  ariaLabel,
  isClickable,
  tint,
  onRowClick,
  routeCode,
  children,
}: RowButtonProps): JSX.Element {
  const baseStyle: React.CSSProperties = {
    display: "grid",
    gridTemplateColumns: "24px 1fr auto auto",
    alignItems: "center",
    gap: "0 8px",
    width: "100%",
    height: 36,
    padding: "8px 12px",
    border: "none",
    background: "transparent",
    textAlign: "left",
    cursor: isClickable ? "pointer" : "default",
    boxSizing: "border-box",
  };

  function handleMouseEnter(e: React.MouseEvent<HTMLButtonElement>) {
    (e.currentTarget as HTMLButtonElement).style.background = HOVER_BG_SOFT;
  }

  function handleMouseLeave(e: React.MouseEvent<HTMLButtonElement>) {
    (e.currentTarget as HTMLButtonElement).style.background = tint ?? "transparent";
  }

  return (
    <button
      type="button"
      aria-label={ariaLabel}
      style={baseStyle}
      onClick={isClickable ? () => onRowClick?.(routeCode) : undefined}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {children}
    </button>
  );
}
