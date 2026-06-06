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
};

/**
 * Floating, draggable legend for the map. Lives over the MapLibre canvas as a
 * fixed-position child of the map container. Position persists in localStorage
 * across reloads. Header strip is the drag handle.
 *
 * Clicking a delay-ramp swatch focuses that severity band: matching circles
 * keep their full severity-floored opacity; circles outside the band go to
 * opacity 0 (fully invisible). Click the same band again, or 選択を解除, // i18n-ignore: JSDoc
 * to clear focus.
 */
export function MapLegend({
  showSingleSampleStops,
  onShowSingleSampleStopsChange,
  focusedSeverity,
  onFocusedSeverityChange,
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

  return (
    <div
      style={{
        position: "absolute",
        left: pos.x,
        top: pos.y,
        zIndex: 5,
        background: "rgba(255,255,255,0.96)",
        border: "1px solid var(--border-subtle)",
        borderRadius: 6,
        boxShadow: "0 2px 12px rgba(0,0,0,0.10)",
        fontSize: 11,
        color: "var(--text-secondary)",
        minWidth: 168,
        userSelect: "none",
      }}
    >
      <div
        role="button"
        tabIndex={0}
        aria-label={t("map.legend.drag_handle")}
        onMouseDown={startDrag}
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
            selected={focusedSeverity === "ok"}
            onClick={() => onFocusedSeverityChange(focusedSeverity === "ok" ? null : "ok")}
          />
          <Row
            color={DELAY_RAMP.mild}
            label={t("map.legend.band_2_5")}
            selected={focusedSeverity === "mild"}
            onClick={() => onFocusedSeverityChange(focusedSeverity === "mild" ? null : "mild")}
          />
          <Row
            color={DELAY_RAMP.moderate}
            label={t("map.legend.band_5_10")}
            selected={focusedSeverity === "moderate"}
            onClick={() => onFocusedSeverityChange(focusedSeverity === "moderate" ? null : "moderate")}
          />
          <Row
            color={DELAY_RAMP.severe}
            label={t("map.legend.band_gt_10")}
            selected={focusedSeverity === "severe"}
            onClick={() => onFocusedSeverityChange(focusedSeverity === "severe" ? null : "severe")}
          />
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
            <Dot diameter={6} opacity={0.4} />
            <Dot diameter={9} opacity={0.65} />
            <Dot diameter={12} opacity={0.85} />
            <span style={{ color: "var(--text-tertiary)" }}>{t("map.legend.few_to_many")}</span>
          </div>
          <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-tertiary)", lineHeight: 1.4 }}>
            {t("map.legend.color_explainer")}<br />
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
  selected,
  onClick,
}: {
  color: string;
  label: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
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
        cursor: "pointer",
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
    </button>
  );
}

function Dot({ diameter, opacity }: { diameter: number; opacity: number }) {
  return (
    <span
      style={{
        width: diameter,
        height: diameter,
        background: DELAY_RAMP.moderate,
        borderRadius: "50%",
        opacity,
      }}
    />
  );
}
