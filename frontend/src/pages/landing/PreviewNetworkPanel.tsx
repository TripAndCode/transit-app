import { useTranslation } from "react-i18next";
import { PREVIEW_AGENCIES } from "./previewData";

/** Dashboard-preview Network tab: the same three preview agencies as the
 *  agency picker, ranked by on-time rate -- stands in for the real
 *  `NetworkTab`'s cross-agency comparison table, reusing its `network.col_*`
 *  labels since they already say exactly what these numbers are. */
export function PreviewNetworkPanel() {
  const { t } = useTranslation();
  const ranked = [...PREVIEW_AGENCIES].sort((a, b) => b.onTimePct - a.onTimePct);

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
      {ranked.map((agency, i) => (
        <div
          key={agency.key}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "10px 12px",
            background: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius)",
            fontSize: 13,
          }}
        >
          <span style={{ width: 18, color: "var(--text-tertiary)" }}>#{i + 1}</span>
          <span style={{ fontWeight: 600 }}>{t(agency.nameKey)}</span>
          <span style={{ marginLeft: "auto", color: "var(--text-tertiary)" }}>
            {t("network.col_avg_delay")}: {agency.avgDelayMin.toFixed(1)}
          </span>
          <span style={{ color: "var(--text-secondary)" }}>
            {t("network.col_on_time")}: {agency.onTimePct}%
          </span>
        </div>
      ))}
    </div>
  );
}
