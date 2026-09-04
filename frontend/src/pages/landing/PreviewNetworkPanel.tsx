import { useTranslation } from "react-i18next";
import { onActivateKey } from "../../utils/a11y";
import { PREVIEW_AGENCIES, type PreviewAgencyKey } from "./previewData";

/** Dashboard-preview Network/Agencies tab: a compact cross-agency
 *  comparison table. Clicking a row genuinely changes the selected agency
 *  (lifted to `DashboardPreview` so the Overview panel's stats follow it
 *  too) rather than just visually toggling in place. */
export function PreviewNetworkPanel({
  selectedKey,
  onSelect,
}: {
  selectedKey: PreviewAgencyKey;
  onSelect: (key: PreviewAgencyKey) => void;
}) {
  const { t } = useTranslation();

  return (
    <div style={{ padding: 16 }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr style={{ textAlign: "left", color: "var(--text-tertiary)", fontSize: 11, textTransform: "uppercase" }}>
            <th style={{ padding: "6px 10px" }}>{t("network.col_agency")}</th>
            <th style={{ padding: "6px 10px" }}>{t("network.col_avg_delay")}</th>
            <th style={{ padding: "6px 10px" }}>{t("network.col_on_time")}</th>
          </tr>
        </thead>
        <tbody>
          {PREVIEW_AGENCIES.map((agency) => {
            const isSelected = agency.key === selectedKey;
            return (
              <tr
                key={agency.key}
                onClick={() => onSelect(agency.key)}
                onKeyDown={onActivateKey(() => onSelect(agency.key))}
                role="button"
                tabIndex={0}
                aria-pressed={isSelected}
                style={{
                  cursor: "pointer",
                  background: isSelected ? "var(--accent-soft)" : "transparent",
                  borderTop: "1px solid var(--border-soft)",
                }}
              >
                <td style={{ padding: "8px 10px", fontWeight: isSelected ? 600 : 400 }}>
                  {t(agency.nameKey)}
                  {isSelected && (
                    <span
                      style={{
                        marginLeft: 8,
                        fontSize: 10,
                        fontWeight: 700,
                        color: "var(--accent)",
                        border: "1px solid var(--accent)",
                        borderRadius: 4,
                        padding: "1px 5px",
                      }}
                    >
                      {t("network.you_badge")}
                    </span>
                  )}
                </td>
                <td style={{ padding: "8px 10px", fontVariantNumeric: "tabular-nums" }}>{agency.avgDelayMin.toFixed(1)}</td>
                <td style={{ padding: "8px 10px", fontVariantNumeric: "tabular-nums" }}>{agency.onTimePct}%</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
