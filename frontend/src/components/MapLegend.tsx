import { useEffect, useRef, useState } from "react";
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
 * opacity 0 (fully invisible). Click the same band again, or 選択を解除,
 * to clear focus.
 */
export function MapLegend({
  showSingleSampleStops,
  onShowSingleSampleStopsChange,
  focusedSeverity,
  onFocusedSeverityChange,
}: MapLegendProps) {
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
        <strong style={{ flex: 1, color: "var(--text-primary)", fontSize: 12 }}>凡例</strong>
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          aria-label={collapsed ? "展開" : "折りたたみ"}
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
            1観測のみも表示
          </label>
          <div style={{ marginBottom: 6, color: "var(--text-tertiary)", letterSpacing: "0.05em", textTransform: "uppercase", fontSize: 10 }}>
            遅延 (平均)
          </div>
          <Row
            color={DELAY_RAMP.ok}
            label="< 2分"
            selected={focusedSeverity === "ok"}
            onClick={() => onFocusedSeverityChange(focusedSeverity === "ok" ? null : "ok")}
          />
          <Row
            color={DELAY_RAMP.mild}
            label="2 – 5分"
            selected={focusedSeverity === "mild"}
            onClick={() => onFocusedSeverityChange(focusedSeverity === "mild" ? null : "mild")}
          />
          <Row
            color={DELAY_RAMP.moderate}
            label="5 – 10分"
            selected={focusedSeverity === "moderate"}
            onClick={() => onFocusedSeverityChange(focusedSeverity === "moderate" ? null : "moderate")}
          />
          <Row
            color={DELAY_RAMP.severe}
            label="> 10分"
            selected={focusedSeverity === "severe"}
            onClick={() => onFocusedSeverityChange(focusedSeverity === "severe" ? null : "severe")}
          />
          {focusedSeverity && (
            <div
              onClick={() => onFocusedSeverityChange(null)}
              style={{
                cursor: "pointer",
                fontSize: 10,
                color: "var(--text-tertiary)",
                marginTop: 4,
                paddingLeft: 20,
              }}
            >
              選択を解除
            </div>
          )}
          <div style={{ marginTop: 8, marginBottom: 6, color: "var(--text-tertiary)", letterSpacing: "0.05em", textTransform: "uppercase", fontSize: 10 }}>
            サイズ・濃度
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Dot diameter={6} opacity={0.4} />
            <Dot diameter={9} opacity={0.65} />
            <Dot diameter={12} opacity={0.85} />
            <span style={{ color: "var(--text-tertiary)" }}>少 → 多</span>
          </div>
          <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-tertiary)", lineHeight: 1.4 }}>
            色: 平均遅延の段階<br />
            円の大きさ・濃度: サンプル数
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
    <div
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
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
    </div>
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
