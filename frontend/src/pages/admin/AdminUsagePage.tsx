import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { DataTable, type DataTableColumn } from "../../components/admin/DataTable";
import { ErrorBanner } from "../../components/ErrorBanner";
import { useAdminUsage, type AdminUsageDay, type AdminUsageRoute } from "../../api/admin";
import { formatDateRange, formatNumber, formatShortDate } from "../../utils/format";
import { PageHeader } from "../../components/ui/PageHeader";

const DAY_OPTIONS = [7, 30, 90] as const;

/** One day's three counts as a stacked bar, scaled to the window's busiest
 *  day -- a reader scanning the shape of traffic over time, not the table
 *  below it which already states each number exactly. */
function DayBar({ day, maxTotal }: { day: AdminUsageDay; maxTotal: number }) {
  const total = day.signed_out + day.signed_in + day.api_key;
  const widthPct = maxTotal > 0 ? Math.round((total / maxTotal) * 100) : 0;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
      <div style={{ width: 90, fontSize: 12, color: "var(--text-secondary)" }}>{formatShortDate(day.day)}</div>
      <div style={{ flex: 1, background: "var(--surface-2)", borderRadius: 4, height: 12, position: "relative" }}>
        <div style={{ width: `${widthPct}%`, background: "var(--accent-soft)", height: "100%", borderRadius: 4 }} />
      </div>
      <div style={{ width: 50, fontSize: 12, color: "var(--text-tertiary)", textAlign: "right" }}>
        {formatNumber(total)}
      </div>
    </div>
  );
}

/** Admin: the cookie-free usage signal -- requests per route template per
 *  day, split by whether the caller was signed out, signed in, or an API
 *  key. Readers never sign in, so this is the only server-side count of
 *  how much the notebook itself is used. */
export function AdminUsagePage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const days = DAY_OPTIONS.includes(Number(searchParams.get("days")) as (typeof DAY_OPTIONS)[number])
    ? Number(searchParams.get("days"))
    : 30;
  const { data, error, refetch } = useAdminUsage({ days });

  function setDays(next: number) {
    const params = new URLSearchParams(searchParams);
    params.set("days", String(next));
    setSearchParams(params);
  }

  const routeColumns: DataTableColumn<AdminUsageRoute>[] = [
    { key: "route", header: t("admin.usage.col.route"), render: (row) => row.route },
    { key: "method", header: t("admin.usage.col.method"), render: (row) => row.method },
    {
      key: "signed_out",
      header: t("admin.usage.col.signed_out"),
      align: "right",
      render: (row) => formatNumber(row.signed_out),
    },
    {
      key: "signed_in",
      header: t("admin.usage.col.signed_in"),
      align: "right",
      render: (row) => formatNumber(row.signed_in),
    },
    {
      key: "api_key",
      header: t("admin.usage.col.api_key"),
      align: "right",
      render: (row) => formatNumber(row.api_key),
    },
    { key: "errors", header: t("admin.usage.col.errors"), align: "right", render: (row) => formatNumber(row.errors) },
  ];

  const maxDayTotal = Math.max(1, ...(data?.days.map((d) => d.signed_out + d.signed_in + d.api_key) ?? [1]));

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <PageHeader
        title={t("admin.usage.title")}
        subtitle={data != null ? formatDateRange(data.from, data.to) : undefined}
      />

      <div style={{ display: "flex", gap: 8, marginBottom: 16 }} role="group" aria-label={t("admin.usage.days_label")}>
        {DAY_OPTIONS.map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={option === days}
            onClick={() => setDays(option)}
            style={{
              fontSize: 13,
              padding: "4px 12px",
              borderRadius: 999,
              cursor: "pointer",
              fontFamily: "inherit",
              border: `1px solid ${option === days ? "var(--accent)" : "var(--border-subtle)"}`,
              background: option === days ? "var(--accent-soft)" : "transparent",
              color: option === days ? "var(--accent-strong)" : "var(--text-secondary)",
            }}
          >
            {t("admin.usage.days_option", { count: option })}
          </button>
        ))}
      </div>

      {error != null && <ErrorBanner error={error} onRetry={() => refetch()} />}

      {data == null && error == null && (
        <div style={{ fontSize: 13, color: "var(--text-tertiary)" }}>{t("common.loading")}</div>
      )}

      {data != null && (
        <>
          <section
            style={{ marginBottom: 24, padding: 16, background: "var(--surface-1)", borderRadius: "var(--radius-lg)" }}
          >
            <h2 style={{ fontSize: 15, marginBottom: 12 }}>{t("admin.usage.by_day.title")}</h2>
            {data.days.length === 0 ? (
              <p style={{ fontSize: 13, color: "var(--text-tertiary)" }}>{t("admin.usage.by_day.empty")}</p>
            ) : (
              data.days.map((day) => <DayBar key={day.day} day={day} maxTotal={maxDayTotal} />)
            )}
          </section>

          <section style={{ padding: 16, background: "var(--surface-1)", borderRadius: "var(--radius-lg)" }}>
            <h2 style={{ fontSize: 15, marginBottom: 12 }}>{t("admin.usage.by_route.title")}</h2>
            <DataTable
              caption={t("admin.usage.by_route.table_label")}
              rows={data.routes}
              columns={routeColumns}
              rowKey={(row) => `${row.route}:${row.method}`}
              emptyLabel={t("admin.usage.by_route.empty")}
              settled
            />
          </section>
        </>
      )}
    </div>
  );
}
