import { useState } from "react";
import { useTranslation } from "react-i18next";
import { delayColor } from "../../styles/tokens";
import { DOW_KEYS, PREVIEW_HOURLY, PREVIEW_TREND_BY_DOW } from "./previewData";

type AnalysisMode = "trend" | "hourly";

const MAX_TREND = Math.max(...PREVIEW_TREND_BY_DOW);
const MAX_HOURLY = Math.max(...PREVIEW_HOURLY.map((h) => h.delayMin));

/** Dashboard-preview Analysis tab: a trend/hour toggle that genuinely swaps
 *  which figures render below it (a bar-per-day trend vs. a handful of
 *  representative hourly cells), reusing the real `map.route_mode.*` labels
 *  since the underlying "historical trend vs. by hour" distinction is the
 *  same one `RouteModeToggle` already names for the Map tab. */
export function PreviewAnalysisPanel() {
  const { t } = useTranslation();
  const [mode, setMode] = useState<AnalysisMode>("trend");

  return (
    <div style={{ padding: 16 }}>
      <div
        role="group"
        aria-label={t("map.route_mode.aria_label")}
        style={{
          display: "inline-flex",
          background: "var(--bg-surface)",
          border: "1px solid var(--border-soft)",
          borderRadius: 8,
          overflow: "hidden",
          marginBottom: 14,
        }}
      >
        {(["trend", "hourly"] as const).map((m) => (
          <button
            key={m}
            type="button"
            aria-pressed={mode === m}
            onClick={() => setMode(m)}
            style={{
              border: "none",
              background: mode === m ? "var(--accent-soft)" : "transparent",
              color: mode === m ? "var(--accent)" : "var(--text-secondary)",
              fontWeight: mode === m ? 600 : 500,
              fontSize: 12,
              padding: "8px 14px",
              cursor: "pointer",
            }}
          >
            {t(`map.route_mode.${m}`)}
          </button>
        ))}
      </div>

      {mode === "trend" ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {PREVIEW_TREND_BY_DOW.map((value, i) => (
            <div key={DOW_KEYS[i]} style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ width: 32, fontSize: 12, color: "var(--text-tertiary)" }}>{t(DOW_KEYS[i])}</span>
              <div style={{ flex: 1, background: "var(--track-bg)", borderRadius: 4, height: 10 }}>
                <div
                  style={{
                    width: `${(value / MAX_TREND) * 100}%`,
                    height: "100%",
                    borderRadius: 4,
                    background: delayColor(value),
                  }}
                />
              </div>
              <span style={{ width: 40, fontSize: 12, color: "var(--text-secondary)", textAlign: "right" }}>
                {value.toFixed(1)}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ display: "flex", gap: 8 }}>
          {PREVIEW_HOURLY.map(({ hour, delayMin }) => (
            <div key={hour} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, flex: 1 }}>
              <div
                style={{
                  width: "100%",
                  height: 60,
                  borderRadius: 4,
                  background: delayColor(delayMin),
                  opacity: 0.35 + 0.65 * (delayMin / MAX_HOURLY),
                }}
              />
              <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{hour}:00</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
