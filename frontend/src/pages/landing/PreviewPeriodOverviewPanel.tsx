import { useTranslation } from "react-i18next";
import { delayColor } from "../../styles/tokens";
import { PREVIEW_AGENCIES, PREVIEW_HOURLY, type PreviewAgencyKey } from "./previewData";

const MAX_HOURLY = Math.max(...PREVIEW_HOURLY.map((h) => h.delayMin));

const statTile = {
  flex: 1,
  minWidth: 120,
  background: "var(--bg-surface)",
  border: "1px solid var(--border-subtle)",
  borderRadius: "var(--radius-lg)",
  padding: "12px 14px",
} as const;

/** Dashboard-preview Period overview tab: the selected agency's headline
 *  stats plus a peak-hour ribbon -- stands in for the real `OverviewTab`'s
 *  headline delta and peak-hour module, reusing the real `network.col_*`
 *  labels and `PREVIEW_HOURLY` dataset already used by the Segment analysis
 *  preview's hourly view. */
export function PreviewPeriodOverviewPanel({ agencyKey }: { agencyKey: PreviewAgencyKey }) {
  const { t } = useTranslation();
  const agency = PREVIEW_AGENCIES.find((a) => a.key === agencyKey) ?? PREVIEW_AGENCIES[0];

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
        <div style={statTile}>
          <div style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>{t("network.col_avg_delay")}</div>
          <div style={{ fontSize: 22, fontWeight: 600, color: "var(--text-primary)" }}>
            {agency.avgDelayMin.toFixed(1)}
          </div>
        </div>
        <div style={statTile}>
          <div style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>{t("network.col_on_time")}</div>
          <div style={{ fontSize: 22, fontWeight: 600, color: "var(--text-primary)" }}>{agency.onTimePct}%</div>
        </div>
      </div>

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
            <span style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>{hour}:00</span>
          </div>
        ))}
      </div>
    </div>
  );
}
