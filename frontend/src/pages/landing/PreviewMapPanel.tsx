import { useState } from "react";
import { useTranslation } from "react-i18next";
import { PreviewMapCanvas } from "./PreviewMapCanvas";

type StyleVariant = "standard" | "muted";
type HeatmapField = "avg" | "p90";

// Real, visible per-toggle CSS -- no canvas-drawing changes needed. "Muted"
// desaturates/dims the scene (mirrors switching to a quieter basemap);
// selecting the 90th-percentile field boosts saturation/contrast (mirrors
// the real heatmap field emphasizing the tail of the distribution).
function filterFor(styleVariant: StyleVariant, heatmapField: HeatmapField): string {
  const parts: string[] = [];
  if (styleVariant === "muted") parts.push("grayscale(0.45)", "brightness(0.92)");
  if (heatmapField === "p90") parts.push("saturate(1.6)", "contrast(1.12)");
  return parts.length > 0 ? parts.join(" ") : "none";
}

/** Dashboard-preview Map tab: a full-bleed panel (matching the real
 *  `MapTab`'s `position:absolute; inset:0` container) with floating overlay
 *  controls -- a style switcher, a heatmap-field toggle, and a legend --
 *  instead of item 63's centered descriptive text + thumbnail. Every
 *  control here visibly changes the rendered scene; none is decorative. */
export function PreviewMapPanel() {
  const { t } = useTranslation();
  const [styleVariant, setStyleVariant] = useState<StyleVariant>("standard");
  const [heatmapField, setHeatmapField] = useState<HeatmapField>("avg");

  return (
    <div style={{ position: "absolute", inset: 0, overflow: "hidden", background: "var(--bg-page)" }}>
      <PreviewMapCanvas filterCss={filterFor(styleVariant, heatmapField)} />

      {/* Legend -- top-left, matching the real MapLegend's badge placement. */}
      <div
        style={{
          position: "absolute",
          left: 12,
          top: 12,
          zIndex: 1,
          background: "var(--map-badge-bg)",
          backdropFilter: "blur(6px)",
          border: "1px solid var(--border-subtle)",
          borderRadius: 8,
          boxShadow: "0 2px 12px rgba(0,0,0,0.10)",
          padding: "8px 10px",
          fontSize: 11,
          color: "var(--text-secondary)",
          minWidth: 140,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          <span style={{ width: 10, height: 10, borderRadius: "50%", background: "var(--accent)", flexShrink: 0 }} />
          <span>{t("landing.preview.map.legend_on_time")}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 10, height: 10, borderRadius: "50%", background: "var(--color-warning)", flexShrink: 0 }} />
          <span>{t("landing.preview.map.legend_delayed")}</span>
        </div>
      </div>

      {/* Style switcher + heatmap-field toggle -- bottom-left, matching the
          real MapStyleControl / heatmap-mode button's placement. */}
      <div style={{ position: "absolute", left: 12, bottom: 12, zIndex: 1, display: "flex", gap: 8 }}>
        <div
          role="group"
          aria-label={t("map.style.label")}
          style={{
            display: "flex",
            background: "#ffffff",
            border: "1px solid rgba(0,0,0,0.14)",
            borderRadius: 8,
            boxShadow: "0 3px 14px rgba(0,0,0,0.28)",
            overflow: "hidden",
          }}
        >
          {(["standard", "muted"] as const).map((variant) => (
            <button
              key={variant}
              type="button"
              aria-pressed={styleVariant === variant}
              onClick={() => setStyleVariant(variant)}
              style={{
                border: "none",
                background: styleVariant === variant ? "var(--accent-soft)" : "transparent",
                color: styleVariant === variant ? "var(--accent)" : "#333",
                fontWeight: styleVariant === variant ? 700 : 500,
                fontSize: 12,
                padding: "8px 12px",
                cursor: "pointer",
              }}
            >
              {t(`landing.preview.map.style_${variant}`)}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => setHeatmapField((f) => (f === "avg" ? "p90" : "avg"))}
          title={t(`map.heatmapMode.${heatmapField === "avg" ? "p90" : "avg"}`)}
          style={{
            padding: "8px 12px",
            fontSize: 12,
            background: heatmapField === "p90" ? "var(--accent-soft)" : "#ffffff",
            border: "1px solid rgba(0,0,0,0.14)",
            borderRadius: 8,
            boxShadow: "0 3px 14px rgba(0,0,0,0.28)",
            cursor: "pointer",
            color: heatmapField === "p90" ? "var(--accent)" : "#333",
            fontWeight: 600,
          }}
        >
          {t(`map.heatmapMode.${heatmapField}`)}
        </button>
      </div>
    </div>
  );
}
