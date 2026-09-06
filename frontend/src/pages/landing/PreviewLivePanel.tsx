import { useState } from "react";
import { useTranslation } from "react-i18next";
import { delayColor } from "../../styles/tokens";
import { PREVIEW_OBSERVATIONS } from "./previewData";

type SortBy = "latest" | "delay";

/** Dashboard-preview Latest-observations tab: a sort toggle that genuinely
 *  reorders the mock feed below it (by recency vs. by delay severity), so
 *  the control does something rather than just sitting there. */
export function PreviewLivePanel() {
  const { t } = useTranslation();
  const [sortBy, setSortBy] = useState<SortBy>("latest");

  const sorted = [...PREVIEW_OBSERVATIONS].sort((a, b) =>
    sortBy === "latest" ? a.minutesAgo - b.minutesAgo : b.delayMin - a.delayMin,
  );

  return (
    <div style={{ padding: 16 }}>
      <div role="group" aria-label={t("live.title")} style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        {(["latest", "delay"] as const).map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={sortBy === option}
            onClick={() => setSortBy(option)}
            style={{
              background: sortBy === option ? "var(--accent-soft)" : "var(--bg-surface)",
              color: sortBy === option ? "var(--accent)" : "var(--text-secondary)",
              border: `1px solid ${sortBy === option ? "var(--accent)" : "var(--border-soft)"}`,
              borderRadius: 999,
              padding: "5px 12px",
              fontSize: 12,
              fontWeight: sortBy === option ? 600 : 400,
              cursor: "pointer",
            }}
          >
            {t(`landing.preview.live.sort_${option}`)}
          </button>
        ))}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {sorted.map((obs, i) => (
          <div
            key={`${obs.routeCode}-${obs.stopN}-${i}`}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "8px 12px",
              background: "var(--bg-surface)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "var(--radius)",
              fontSize: 13,
            }}
          >
            <span style={{ width: 10, height: 10, borderRadius: "50%", background: delayColor(obs.delayMin), flexShrink: 0 }} />
            <span style={{ fontWeight: 600 }}>{t("landing.preview.route_label", { code: obs.routeCode })}</span>
            <span style={{ color: "var(--text-tertiary)" }}>{t("landing.preview.live.stop_label", { n: obs.stopN })}</span>
            <span style={{ marginLeft: "auto", color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>
              {obs.delayMin.toFixed(1)} {t("forecast.axis_min")}
            </span>
            <span style={{ color: "var(--text-tertiary)", fontSize: 11 }}>
              {t("landing.preview.live.minutes_ago", { n: obs.minutesAgo })}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
