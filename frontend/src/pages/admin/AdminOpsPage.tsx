import { useTranslation } from "react-i18next";
import { useAdminOps, type AgencyFreshnessItem } from "../../api/admin";
import { StatusChip } from "./adminControls";

function formatAge(t: ReturnType<typeof useTranslation>["t"], ageHours: number | null): string {
  if (ageHours === null) return t("admin.ops.unknown");
  if (ageHours < 1) return t("admin.ops.age_minutes", { m: Math.round(ageHours * 60) });
  return t("admin.ops.age_hours", { h: ageHours.toFixed(1) });
}

function FreshnessChip({ row, t }: { row: AgencyFreshnessItem; t: ReturnType<typeof useTranslation>["t"] }) {
  if (row.last_analyzed_at === null) {
    return <StatusChip tone="neutral">{t("admin.ops.never")}</StatusChip>;
  }
  if (row.agg_fresh) {
    return <StatusChip tone="good">{t("admin.ops.fresh")}</StatusChip>;
  }
  return <StatusChip tone="warn">{t("admin.ops.behind", { days: row.agg_behind_days })}</StatusChip>;
}

type StripStatus = "loading" | "ok" | "warn" | "unknown";

function stripDotColor(status: StripStatus): string {
  return status === "warn" || status === "unknown" ? "var(--color-warning, #C99A2E)" : "var(--accent)";
}

export function AdminOpsPage() {
  const { t } = useTranslation();
  const { data, error } = useAdminOps();

  const staleCount = data?.agencies.filter((a) => !a.agg_fresh).length ?? 0;
  const migBehind = data?.migrations?.behind ?? 0;

  // `migrations === null` / `agencies_ok === false` means that sub-check
  // threw server-side (degrade-gracefully-but-still-200) — render it as
  // "unknown", not as a false "0 behind" / "all fresh" green state. A failed
  // request (`error`, no `data` at all) is also "unknown", not stuck loading.
  const migStatus: StripStatus =
    data === undefined
      ? error
        ? "unknown"
        : "loading"
      : data.migrations === null
      ? "unknown"
      : migBehind > 0
      ? "warn"
      : "ok";
  const agenciesStatus: StripStatus =
    data === undefined
      ? error
        ? "unknown"
        : "loading"
      : !data.agencies_ok
      ? "unknown"
      : staleCount > 0
      ? "warn"
      : "ok";

  const migText: Record<StripStatus, string> = {
    loading: t("common.loading"),
    unknown: t("admin.ops.migrations_unknown"),
    warn: t("admin.ops.migrations_behind", { count: migBehind }),
    ok: t("admin.ops.migrations_ok"),
  };
  const agenciesText: Record<StripStatus, string> = {
    loading: t("common.loading"),
    unknown: t("admin.ops.agencies_unknown"),
    warn: t("admin.ops.agencies_stale", { count: staleCount }),
    ok: t("admin.ops.agencies_all_fresh"),
  };

  return (
    <div style={{ padding: 24, maxWidth: 900 }}>
      <h1 style={{ fontSize: 22, marginBottom: 20 }}>{t("admin.ops.title")}</h1>

      {error && (
        <p
          role="alert"
          style={{
            marginBottom: 16, padding: "10px 14px", borderRadius: "var(--radius-md)",
            background: "var(--surface-1)", color: "var(--color-warning, #C99A2E)", fontSize: 14,
          }}
        >
          {t("admin.ops.load_error")}
        </p>
      )}

      {/* Global status strip */}
      <div
        style={{
          display: "flex", alignItems: "center", gap: 20, marginBottom: 24, padding: "12px 16px",
          background: "var(--surface-1)", borderRadius: "var(--radius-md)",
          fontSize: 14,
        }}
      >
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span aria-hidden style={{ width: 8, height: 8, borderRadius: "50%", background: stripDotColor(migStatus), flexShrink: 0 }} />
          {migText[migStatus]}
        </span>
        <span style={{ color: "var(--border-soft)" }}>|</span>
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span aria-hidden style={{ width: 8, height: 8, borderRadius: "50%", background: stripDotColor(agenciesStatus), flexShrink: 0 }} />
          {agenciesText[agenciesStatus]}
        </span>
      </div>

      {/* Per-agency table */}
      <table className="admin-table">
        <thead>
          <tr>
            <th>{t("admin.ops.col_agency")}</th>
            <th>{t("admin.ops.col_last_analyzed")}</th>
            <th>{t("admin.ops.col_freshness")}</th>
            <th>{t("admin.ops.col_data_to")}</th>
            <th>{t("admin.ops.col_clamp_pct")}</th>
          </tr>
        </thead>
        <tbody>
          {data?.agencies.map((a) => (
            <tr key={a.agency_id}>
              <td style={{ fontWeight: 500 }}>{a.agency_name}</td>
              <td style={{ color: "var(--text-tertiary)", fontSize: 13 }}>
                {formatAge(t, a.analyze_age_hours)}
              </td>
              <td>
                <FreshnessChip row={a} t={t} />
              </td>
              <td style={{ color: "var(--text-tertiary)", fontSize: 13 }}>
                {a.data_to ?? t("admin.ops.unknown")}
              </td>
              <td style={{ color: "var(--text-tertiary)", fontSize: 13 }}>
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
