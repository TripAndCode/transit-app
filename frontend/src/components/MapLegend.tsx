import { useState } from "react";
import { useTranslation } from "react-i18next";
import { DELAY_RAMP } from "../styles/tokens";
import { useMediaQuery, MOBILE_BREAKPOINT_QUERY } from "../hooks/useMediaQuery";

export type SeverityKey = "ok" | "mild" | "moderate" | "severe";

type MapLegendProps = {
  showSingleSampleStops: boolean;
  onShowSingleSampleStopsChange: (v: boolean) => void;
  focusedSeverity: SeverityKey | null;
  onFocusedSeverityChange: (s: SeverityKey | null) => void;
  /** Stop count per band; a band with 0 is shown but disabled (clicking it
   *  would just blank the map). Omitted → all bands enabled, no counts. */
  bandCounts?: Record<SeverityKey, number>;
};

/**
 * Fixed, translucent-blurred legend badge in the map's top-left corner,
 * matching the artifact mockup (docs/superpowers/specs/2026-07-11-artifact-design-parity-design.md).
 * Not draggable — that stays removed as chrome, not real functionality.
 * Collapsible (added back 2026-07-18: the legend takes up more noticeable
 * map space now the map fills the full viewport height) via a chevron
 * toggle in the header; starts expanded on every mount, no persistence,
 * except below the shared MOBILE_BREAKPOINT_QUERY (see useMediaQuery.ts)
 * where it starts collapsed — on a phone-width first paint the expanded
 * panel otherwise covers nearly the entire map, which is the whole point
 * of this tab (item 28, 2026-08-30).
 * Everything else (click-to-filter, per-band counts, the single-sample-stops
 * checkbox, the size/density key, the no-data key, and the explainer text)
 * stays exactly as before.
 *
 * Clicking a delay-ramp swatch focuses that severity band: the map is FILTERED
 * to stops in that band (cluster bubbles + dots re-form from only those stops),
 * not merely dimmed. Bands with zero stops are shown but disabled.
 */
export function MapLegend({
  showSingleSampleStops,
  onShowSingleSampleStopsChange,
  focusedSeverity,
  onFocusedSeverityChange,
  bandCounts,
}: MapLegendProps) {
  const { t } = useTranslation();
  const isMobile = useMediaQuery(MOBILE_BREAKPOINT_QUERY);
  // Default collapsed on phone-width first paint (isMobile is already correct
  // synchronously — see useMediaQuery's doc comment), expanded everywhere
  // else. Only the initial value is derived from the viewport; toggling
  // still has no persistence and resizing after mount doesn't re-collapse
  // or re-expand it.
  const [collapsed, setCollapsed] = useState(isMobile);

  return (
    <div
      style={{
        position: "absolute",
        left: 16,
        top: 16,
        zIndex: 5,
        background: "var(--map-badge-bg)",
        backdropFilter: "blur(6px)",
        border: "1px solid var(--border-subtle)",
        borderRadius: 8,
        boxShadow: "0 2px 12px rgba(0,0,0,0.10)",
        fontSize: 11,
        color: "var(--text-secondary)",
        minWidth: 168,
        userSelect: "none",
      }}
    >
      <div
        style={{
          padding: "5px 8px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
          borderBottom: collapsed ? "none" : "1px solid var(--border-soft)",
        }}
      >
        <strong style={{ color: "var(--text-primary)", fontSize: 12 }}>{t("map.legend.title")}</strong>
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          aria-label={collapsed ? t("map.legend.expand") : t("map.legend.collapse")}
          style={{
            appearance: "none",
            background: "transparent",
            border: "none",
            cursor: "pointer",
            color: "var(--text-secondary)",
            fontSize: 12,
            lineHeight: 1,
            padding: 2,
          }}
        >
          {collapsed ? "▸" : "▾"}
        </button>
      </div>
      {!collapsed && (
      <div style={{ padding: "8px 10px" }}>
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: 11,
            color: "var(--text-secondary)",
            cursor: "pointer",
            marginBottom: 8,
          }}
        >
          <input
            type="checkbox"
            checked={showSingleSampleStops}
            onChange={(e) => onShowSingleSampleStopsChange(e.target.checked)}
          />
          {t("map.legend.show_single_sample")}
        </label>
        <div style={{ marginBottom: 6, color: "var(--text-tertiary)", letterSpacing: "0.05em", textTransform: "uppercase", fontSize: 10 }}>
          {t("map.legend.delay_avg")}
        </div>
        <Row
          color={DELAY_RAMP.ok}
          label={t("map.legend.band_lt_1_5")}
          count={bandCounts?.ok}
          selected={focusedSeverity === "ok"}
          onClick={() => onFocusedSeverityChange(focusedSeverity === "ok" ? null : "ok")}
        />
        <Row
          color={DELAY_RAMP.mild}
          label={t("map.legend.band_1_5_3")}
          count={bandCounts?.mild}
          selected={focusedSeverity === "mild"}
          onClick={() => onFocusedSeverityChange(focusedSeverity === "mild" ? null : "mild")}
        />
        <Row
          color={DELAY_RAMP.moderate}
          label={t("map.legend.band_3_5")}
          count={bandCounts?.moderate}
          selected={focusedSeverity === "moderate"}
          onClick={() => onFocusedSeverityChange(focusedSeverity === "moderate" ? null : "moderate")}
        />
        <Row
          color={DELAY_RAMP.severe}
          label={t("map.legend.band_gt_5")}
          count={bandCounts?.severe}
          selected={focusedSeverity === "severe"}
          onClick={() => onFocusedSeverityChange(focusedSeverity === "severe" ? null : "severe")}
        />
        {/* Unobserved route-stops render as hollow rings in single-route mode;
            explain them here. Not a focusable severity band — purely a key. */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 3, padding: "2px 4px" }}>
          <span
            style={{
              width: 12,
              height: 12,
              borderRadius: "50%",
              background: "transparent",
              border: "1px solid rgba(0,0,0,0.35)",
              flexShrink: 0,
            }}
          />
          <span>{t("map.legend.no_data")}</span>
        </div>
        {focusedSeverity && (
          <button
            type="button"
            onClick={() => onFocusedSeverityChange(null)}
            style={{
              appearance: "none",
              background: "transparent",
              border: "none",
              font: "inherit",
              textAlign: "left",
              display: "block",
              cursor: "pointer",
              fontSize: 10,
              color: "var(--text-tertiary)",
              marginTop: 4,
              paddingLeft: 20,
            }}
          >
            {t("map.legend.clear_selection")}
          </button>
        )}
        <div style={{ marginTop: 8, marginBottom: 6, color: "var(--text-tertiary)", letterSpacing: "0.05em", textTransform: "uppercase", fontSize: 10 }}>
          {t("map.legend.size_density")}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Dot diameter={6} color={DELAY_RAMP.ok} />
          <Dot diameter={10} color={DELAY_RAMP.mild} />
          <Dot diameter={14} color={DELAY_RAMP.moderate} />
          <span style={{ color: "var(--text-tertiary)" }}>{t("map.legend.few_to_many")}</span>
        </div>
        <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-tertiary)", lineHeight: 1.4 }}>
          {t("map.legend.color_explainer")}<br />
          {t("map.legend.bubble_explainer")}<br />
          {t("map.legend.size_explainer")}
        </div>
      </div>
      )}
    </div>
  );
}

function Row({
  color,
  label,
  count,
  selected,
  onClick,
}: {
  color: string;
  label: string;
  count?: number;
  selected: boolean;
  onClick: () => void;
}) {
  const { t } = useTranslation();
  // A band with zero stops is shown (so the scale stays complete) but disabled —
  // clicking it would filter the map to nothing.
  const disabled = count === 0;
  return (
    <button
      type="button"
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      aria-pressed={selected}
      title={disabled ? t("map.legend.band_empty") : undefined}
      style={{
        appearance: "none",
        border: "none",
        font: "inherit",
        textAlign: "left",
        display: "flex",
        alignItems: "center",
        gap: 8,
        width: "100%",
        marginBottom: 3,
        padding: "2px 4px",
        borderRadius: 4,
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.4 : 1,
        background: selected ? "var(--accent-soft)" : "transparent",
        color: selected ? "var(--accent)" : "inherit",
        fontWeight: selected ? 600 : 400,
        transition: "background var(--transition)",
      }}
    >
      <span
        style={{
          width: 12,
          height: 12,
          background: color,
          borderRadius: "50%",
          flexShrink: 0,
          outline: selected ? "2px solid var(--accent)" : "none",
          outlineOffset: 1,
        }}
      />
      <span>{label}</span>
      {count !== undefined && (
        <span style={{ marginLeft: "auto", color: "var(--text-tertiary)", fontVariantNumeric: "tabular-nums" }}>
          {count}
        </span>
      )}
    </button>
  );
}

function Dot({
  diameter,
  opacity = 1,
  color = DELAY_RAMP.moderate,
}: {
  diameter: number;
  opacity?: number;
  color?: string;
}) {
  return (
    <span
      style={{
        width: diameter,
        height: diameter,
        background: color,
        borderRadius: "50%",
        opacity,
      }}
    />
  );
}
