import { useTranslation } from "react-i18next";
import { useAdminOps, type AgencyFreshnessItem } from "../../api/admin";

function formatAge(t: ReturnType<typeof useTranslation>["t"], ageHours: number | null): string {
  if (ageHours === null) return t("admin.ops.unknown");
  if (ageHours < 1) return t("admin.ops.age_minutes", { m: Math.round(ageHours * 60) });
  return t("admin.ops.age_hours", { h: ageHours.toFixed(1) });
}

function FreshnessChip({ row, t }: { row: AgencyFreshnessItem; t: ReturnType<typeof useTranslation>["t"] }) {
  if (row.last_analyzed_at === null) {
    return (
      <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{t("admin.ops.never")}</span>
    );
  }
  if (row.agg_fresh) {
    return (
      <span style={{ fontSize: 12, padding: "2px 8px", borderRadius: 4, background: "var(--accent-soft)", color: "var(--accent)" }}>
        {t("admin.ops.fresh")}
      </span>
    );
  }
  return (
    <span style={{ fontSize: 12, padding: "2px 8px", borderRadius: 4, background: "var(--surface-2)", color: "var(--color-warning, #C99A2E)" }}>
      {t("admin.ops.behind", { days: row.agg_behind_days })}
    </span>
  );
}

export function AdminOpsPage() {
  const { t } = useTranslation();
  const { data, isLoading } = useAdminOps();

  const staleCount = data?.agencies.filter((a) => !a.agg_fresh).length ?? 0;
  const migBehind = data?.migrations?.behind ?? 0;

  return (
    <div style={{ padding: 24, maxWidth: 900 }}>
      <h1 style={{ fontSize: 22, marginBottom: 20 }}>{t("admin.ops.title")}</h1>

      {/* Global status strip */}
      <div
        style={{
          display: "flex", gap: 16, marginBottom: 24, padding: "12px 16px",
          background: "var(--surface-1)", borderRadius: "var(--radius-md)",
          fontSize: 14,
        }}
      >
        <span
          style={{ color: migBehind > 0 ? "var(--color-warning, #C99A2E)" : "var(--accent)" }}
        >
          {data === undefined && isLoading
            ? t("common.loading")
            : migBehind > 0
            ? t("admin.ops.migrations_behind", { count: migBehind })
            : t("admin.ops.migrations_ok")}
        </span>
        <span style={{ color: "var(--border-soft)" }}>|</span>
        <span
          style={{ color: staleCount > 0 ? "var(--color-warning, #C99A2E)" : "var(--accent)" }}
        >
          {staleCount > 0
            ? t("admin.ops.agencies_stale", { count: staleCount })
            : t("admin.ops.agencies_all_fresh")}
        </span>
      </div>

      {/* Per-agency table */}
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
        <thead>
          <tr style={{ background: "var(--surface-1)" }}>
            <th style={{ padding: "8px 12px", textAlign: "left" }}>{t("admin.ops.col_agency")}</th>
            <th style={{ padding: "8px 12px", textAlign: "left" }}>{t("admin.ops.col_last_analyzed")}</th>
            <th style={{ padding: "8px 12px", textAlign: "left" }}>{t("admin.ops.col_freshness")}</th>
            <th style={{ padding: "8px 12px", textAlign: "left" }}>{t("admin.ops.col_data_to")}</th>
            <th style={{ padding: "8px 12px", textAlign: "left" }}>{t("admin.ops.col_clamp_pct")}</th>
          </tr>
        </thead>
        <tbody>
          {data?.agencies.map((a) => (
            <tr key={a.agency_id} style={{ borderBottom: "1px solid var(--surface-2)" }}>
              <td style={{ padding: "8px 12px", fontWeight: 500 }}>{a.agency_name}</td>
              <td style={{ padding: "8px 12px", color: "var(--text-tertiary)", fontSize: 13 }}>
                {formatAge(t, a.analyze_age_hours)}
              </td>
              <td style={{ padding: "8px 12px" }}>
                <FreshnessChip row={a} t={t} />
              </td>
              <td style={{ padding: "8px 12px", color: "var(--text-tertiary)", fontSize: 13 }}>
                {a.data_to ?? t("admin.ops.unknown")}
              </td>
              <td style={{ padding: "8px 12px", color: "var(--text-tertiary)", fontSize: 13 }}>
                {a.clamp_pct !== null ? `${a.clamp_pct}%` : t("admin.ops.unknown")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Muted footnote */}
      <p style={{ marginTop: 24, fontSize: 12, color: "var(--text-tertiary)" }}>
        {t("admin.ops.network_link")}
      </p>
    </div>
  );
}
