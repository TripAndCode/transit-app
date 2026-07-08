import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { DELAY_RAMP } from "../styles/tokens";

type Pos = { x: number; y: number };

const STORAGE_KEY = "map_legend_pos";
const DEFAULT_POS: Pos = { x: 12, y: 12 };

function loadPos(): Pos {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_POS;
    const p = JSON.parse(raw);
    if (typeof p.x === "number" && typeof p.y === "number") return p;
  } catch {
    /* ignore */
  }
  return DEFAULT_POS;
}

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
 * Floating, draggable legend for the map. Lives over the MapLibre canvas as a
 * fixed-position child of the map container. Position persists in localStorage
 * across reloads. Header strip is the drag handle.
 *
 * Clicking a delay-ramp swatch focuses that severity band: the map is FILTERED
 * to stops in that band (cluster bubbles + dots re-form from only those stops),
 * not merely dimmed. Bands with zero stops are shown but disabled. Click the
 * same band again, or 選択を解除, to clear focus. // i18n-ignore: JSDoc
 */
export function MapLegend({
  showSingleSampleStops,
  onShowSingleSampleStopsChange,
  focusedSeverity,
  onFocusedSeverityChange,
  bandCounts,
}: MapLegendProps) {
  const { t } = useTranslation();
  const [pos, setPos] = useState<Pos>(loadPos);
  const [collapsed, setCollapsed] = useState(false);
  const dragRef = useRef<{ dx: number; dy: number } | null>(null);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(pos));
  }, [pos]);

  useEffect(() => {
    function onMove(e: MouseEvent) {
      if (!dragRef.current) return;
      setPos({ x: e.clientX - dragRef.current.dx, y: e.clientY - dragRef.current.dy });
    }
    function onUp() {
      dragRef.current = null;
      document.body.style.userSelect = "";
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  function startDrag(e: React.MouseEvent) {
    dragRef.current = { dx: e.clientX - pos.x, dy: e.clientY - pos.y };
    document.body.style.userSelect = "none";
  }

  /** Keyboard counterpart to the mouse drag: arrow keys nudge the legend. */
  function nudge(e: React.KeyboardEvent) {
    const STEP = 16;
    const delta: Record<string, [number, number]> = {
      ArrowLeft: [-STEP, 0],
      ArrowRight: [STEP, 0],
      ArrowUp: [0, -STEP],
      ArrowDown: [0, STEP],
    };
    const d = delta[e.key];
    if (!d) return;
    e.preventDefault();
    setPos((p) => ({ x: Math.max(0, p.x + d[0]), y: Math.max(0, p.y + d[1]) }));
  }

  return (
    <div
      style={{
        position: "absolute",
        left: pos.x,
        top: pos.y,
        zIndex: 5,
        background: "var(--map-badge-bg)",
        backdropFilter: "blur(6px)",
        WebkitBackdropFilter: "blur(6px)",
        border: "1px solid var(--border-subtle)",
        borderRadius: 8,
        boxShadow: "0 2px 12px rgba(0,0,0,0.10)",
        fontSize: 11,
        color: "var(--text-secondary)",
        minWidth: 168,
        userSelect: "none",
      }}
    >
      {/* Drag handle: mouse drag + keyboard arrow-key nudging. */}
      <div
        role="button"
        tabIndex={0}
        aria-label={t("map.legend.drag_handle")}
        onMouseDown={startDrag}
        onKeyDown={nudge}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "5px 8px",
          borderBottom: collapsed ? "none" : "1px solid var(--border-soft)",
          cursor: "move",
        }}
      >
        <span aria-hidden style={{ fontSize: 10, color: "var(--text-tertiary)" }}>⠿</span>
        <strong style={{ flex: 1, color: "var(--text-primary)", fontSize: 12 }}>{t("map.legend.title")}</strong>
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          aria-label={collapsed ? t("common.expand") : t("common.collapse")}
          style={{
            background: "transparent",
            border: "none",
            color: "var(--text-tertiary)",
            fontSize: 12,
            cursor: "pointer",
            padding: 0,
            width: 16,
            height: 16,
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
            label={t("map.legend.band_lt_2")}
            count={bandCounts?.ok}
            selected={focusedSeverity === "ok"}
            onClick={() => onFocusedSeverityChange(focusedSeverity === "ok" ? null : "ok")}
          />
          <Row
            color={DELAY_RAMP.mild}
            label={t("map.legend.band_2_5")}
            count={bandCounts?.mild}
            selected={focusedSeverity === "mild"}
            onClick={() => onFocusedSeverityChange(focusedSeverity === "mild" ? null : "mild")}
          />
          <Row
            color={DELAY_RAMP.moderate}
            label={t("map.legend.band_5_10")}
            count={bandCounts?.moderate}
            selected={focusedSeverity === "moderate"}
            onClick={() => onFocusedSeverityChange(focusedSeverity === "moderate" ? null : "moderate")}
          />
          <Row
            color={DELAY_RAMP.severe}
            label={t("map.legend.band_gt_10")}
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
