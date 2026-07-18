import { useTranslation } from "react-i18next";

export type RouteMode = "trend" | "hourly";

const MODES: readonly RouteMode[] = ["trend", "hourly"];

/** Small segmented control switching the single-route overlay between its
 *  two color stories: "trend" (per-segment historical delay, the default)
 *  and "hourly" (the hour-scrubber's flat single-value line). Positioned
 *  top-center over the map — the default top-left slot is already used by
 *  the draggable MapLegend, and bottom-left by MapStyleControl / the
 *  heatmap-mode toggle, so top-center is the first open slot, mirroring
 *  MapHourScrubber's bottom-center placement. */
export function RouteModeToggle({
  mode,
  onModeChange,
}: {
  mode: RouteMode;
  onModeChange: (mode: RouteMode) => void;
}) {
  const { t } = useTranslation();

  return (
    <div
      role="group"
      aria-label={t("map.route_mode.aria_label")}
      style={{
        position: "absolute",
        top: 12,
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: 2,
        display: "flex",
        background: "#ffffff",
        border: "1px solid rgba(0,0,0,0.14)",
        borderRadius: 8,
        boxShadow: "0 3px 14px rgba(0,0,0,0.28)",
        overflow: "hidden",
      }}
    >
      {MODES.map((m) => (
        <button
          key={m}
          type="button"
          aria-pressed={mode === m}
          onClick={() => onModeChange(m)}
          style={{
            padding: "6px 12px",
            fontSize: 12,
            fontWeight: mode === m ? 700 : 500,
            background: mode === m ? "var(--accent-soft)" : "transparent",
            color: mode === m ? "var(--chip-accent)" : "var(--chip-text-secondary)",
            border: "none",
            cursor: "pointer",
          }}
        >
          {t(`map.route_mode.${m}`)}
        </button>
      ))}
    </div>
  );
}
