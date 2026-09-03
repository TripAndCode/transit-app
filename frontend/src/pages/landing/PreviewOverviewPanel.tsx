import { useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { delayColor } from "../../styles/tokens";
import { PREVIEW_ROUTES, type PreviewAgencyKey } from "./previewData";

type RouteFilter = "all" | "on_time" | "delayed";

const chipStyle = (active: boolean): CSSProperties => ({
  background: active ? "var(--accent-soft)" : "var(--bg-surface)",
  color: active ? "var(--accent)" : "var(--text-secondary)",
  border: `1px solid ${active ? "var(--accent)" : "var(--border-soft)"}`,
  borderRadius: 999,
  padding: "5px 12px",
  fontSize: 12,
  fontWeight: active ? 600 : 400,
  cursor: "pointer",
  transition: "all var(--transition)",
});

const statTile: CSSProperties = {
  flex: 1,
  minWidth: 120,
  background: "var(--bg-surface)",
  border: "1px solid var(--border-subtle)",
  borderRadius: "var(--radius-lg)",
  padding: "12px 14px",
};

function average(values: number[]): number {
  return values.length === 0 ? 0 : values.reduce((a, b) => a + b, 0) / values.length;
}

/** Dashboard-preview Overview tab: filter chips that genuinely re-filter the
 *  route list below (not decorative pills), plus stat tiles recomputed from
 *  the filtered subset -- reusing the real `network.col_*` labels since
 *  they already say exactly what these numbers are. */
export function PreviewOverviewPanel({ agencyKey }: { agencyKey: PreviewAgencyKey }) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<RouteFilter>("all");
  const routes = PREVIEW_ROUTES[agencyKey];
  const filtered = routes.filter((r) => {
    if (filter === "on_time") return r.onTime;
    if (filter === "delayed") return !r.onTime;
    return true;
  });
  const onTimePct = routes.length > 0 ? Math.round((routes.filter((r) => r.onTime).length / routes.length) * 100) : 0;

  return (
    <div style={{ padding: 20 }}>
      <div role="group" aria-label={t("filters.title")} style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        {(["all", "on_time", "delayed"] as const).map((f) => (
          <button
            key={f}
            type="button"
            aria-pressed={filter === f}
            onClick={() => setFilter(f)}
            style={chipStyle(filter === f)}
          >
            {t(`landing.preview.overview.filter_${f}`)}
          </button>
        ))}
      </div>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 20 }}>
        <div style={statTile}>
          <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{t("network.col_avg_delay")}</div>
          <div style={{ fontSize: 22, fontWeight: 600, color: "var(--text-primary)" }}>
            {average(filtered.map((r) => r.delayMin)).toFixed(1)}
          </div>
        </div>
        <div style={statTile}>
          <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{t("network.col_on_time")}</div>
          <div style={{ fontSize: 22, fontWeight: 600, color: "var(--text-primary)" }}>{onTimePct}%</div>
        </div>
        <div style={statTile}>
          <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{t("network.col_samples")}</div>
          <div style={{ fontSize: 22, fontWeight: 600, color: "var(--text-primary)" }}>{filtered.length}</div>
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {filtered.map((route) => (
          <div
            key={route.code}
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
            <span style={{ width: 10, height: 10, borderRadius: "50%", background: delayColor(route.delayMin), flexShrink: 0 }} />
            <span style={{ fontWeight: 600 }}>{t("landing.preview.route_label", { code: route.code })}</span>
            <span style={{ marginLeft: "auto", color: "var(--text-tertiary)", fontVariantNumeric: "tabular-nums" }}>
              {route.delayMin.toFixed(1)} {t("forecast.axis_min")}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
