import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { AdminAuditItem, AuditSnapshot } from "../../api/admin";
import { DataTable, type DataTableColumn } from "../../components/admin/DataTable";
import {
  fetchAllAdminAudit,
  useAdminAudit,
  type AdminAuditFilters,
} from "../../api/admin";
import { formatApiError } from "../../api/client";
import { formatDateTime } from "../../utils/format";
import { downloadCsv } from "../../components/analysis/csv";
import { AdminButton, AdminSearchInput } from "./adminControls";
import { diffEntries, formatDiffValue } from "./auditDiff";

/** Before→after pills for the fields that actually changed. Renders nothing
 * for a row with no diff data (a merged `login_events` row, or an
 * `admin_audit` action that touched nothing). */
function DiffPills({
  before,
  after,
}: {
  before: AuditSnapshot;
  after: AuditSnapshot;
}) {
  const entries = diffEntries(before, after).filter((e) => e.changed);
  if (entries.length === 0) return <span style={{ color: "var(--text-tertiary)" }}>—</span>;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
      {entries.map((e) => (
        <span
          key={e.key}
          style={{
            fontSize: 12,
            padding: "2px 8px",
            borderRadius: 999,
            background: "var(--surface-2)",
            color: "var(--text-secondary)",
            whiteSpace: "nowrap",
          }}
        >
          <strong>{e.key}</strong>: {formatDiffValue(e.before)} → {formatDiffValue(e.after)}
        </span>
      ))}
    </div>
  );
}

/** Owns cursor-stack pagination for one fixed filter set. Remounted (via a
 * `key` on the filter values) whenever a filter changes, which resets
 * pagination back to the first page without a synchronization effect. */
function AuditTimeline({ filters }: { filters: AdminAuditFilters }) {
  const { t } = useTranslation();
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const pageIndex = cursorStack.length - 1;
  const { data, isLoading, error } = useAdminAudit(filters, cursorStack[pageIndex]);

  function goNext() {
    if (data?.next_cursor) setCursorStack((s) => [...s, data.next_cursor as string]);
  }
  function goPrev() {
    setCursorStack((s) => (s.length > 1 ? s.slice(0, -1) : s));
  }

  const columns: DataTableColumn<AdminAuditItem>[] = [
    { key: "at", header: t("admin.audit.col.at"), render: (item) => formatDateTime(item.at) },
    { key: "actor", header: t("admin.audit.col.actor"), render: (item) => item.actor_id ?? "—" },
    { key: "action", header: t("admin.audit.col.action"), render: (item) => item.action },
    {
      key: "target",
      header: t("admin.audit.col.target"),
      render: (item) => `${item.target_type}${item.target_id ? `:${item.target_id}` : ""}`,
    },
    {
      key: "diff",
      header: t("admin.audit.col.diff"),
      render: (item) => <DiffPills before={item.before} after={item.after} />,
    },
    { key: "reason", header: t("admin.audit.col.reason"), render: (item) => item.reason ?? "—" },
    { key: "ip", header: t("admin.audit.col.ip"), render: (item) => item.ip ?? "—" },
  ];

  return (
    <>
      {error && <div style={{ color: "var(--text-tertiary)" }}>{formatApiError(error)}</div>}
      {isLoading && <div>{t("common.loading")}</div>}
      <DataTable
        caption={t("admin.audit.table_label")}
        rows={data?.items ?? []}
        columns={columns}
        rowKey={(item) => `${item.at}-${item.action}-${item.target_id ?? ""}-${item.actor_id ?? ""}`}
        emptyLabel={t("admin.audit.empty")}
      />
      <div style={{ marginTop: 12, display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <AdminButton variant="secondary" disabled={pageIndex === 0} onClick={goPrev}>
          {t("admin.audit.pagination.prev")}
        </AdminButton>
        <AdminButton variant="secondary" disabled={!data?.next_cursor} onClick={goNext}>
          {t("admin.audit.pagination.next")}
        </AdminButton>
      </div>
    </>
  );
}

/** Admin: unified audit timeline (admin_audit + login/login_failed events),
 * with before→after diff pills, filters, cursor paging, and CSV export. */
export function AdminAuditPage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters: AdminAuditFilters = {
    actor: searchParams.get("actor") ?? undefined,
    target: searchParams.get("target") ?? undefined,
    action: searchParams.get("action") ?? undefined,
    from: searchParams.get("from") ?? undefined,
    to: searchParams.get("to") ?? undefined,
  };
  const filterKey = JSON.stringify(filters);

  function setFilter(key: keyof AdminAuditFilters, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearchParams(next);
  }

  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  async function handleExport() {
    setExporting(true);
    setExportError(null);
    try {
      const items = await fetchAllAdminAudit(filters);
      downloadCsv("admin-audit", [
        [
          t("admin.audit.col.at"),
          t("admin.audit.col.actor"),
          t("admin.audit.col.action"),
          t("admin.audit.col.target"),
          t("admin.audit.col.reason"),
          t("admin.audit.col.ip"),
        ],
        ...items.map((item) => [
          item.at,
          item.actor_id ?? "",
          item.action,
          `${item.target_type}${item.target_id ? `:${item.target_id}` : ""}`,
          item.reason ?? "",
          item.ip ?? "",
        ]),
      ]);
    } catch (e) {
      setExportError(formatApiError(e));
    } finally {
      setExporting(false);
    }
  }

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 22, marginBottom: 16 }}>{t("admin.audit.title")}</h1>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 16 }}>
        <AdminSearchInput
          aria-label={t("admin.audit.filter.actor_placeholder")}
          placeholder={t("admin.audit.filter.actor_placeholder")}
          value={filters.actor ?? ""}
          onChange={(e) => setFilter("actor", e.target.value)}
        />
        <AdminSearchInput
          aria-label={t("admin.audit.filter.target_placeholder")}
          placeholder={t("admin.audit.filter.target_placeholder")}
          value={filters.target ?? ""}
          onChange={(e) => setFilter("target", e.target.value)}
        />
        <AdminSearchInput
          aria-label={t("admin.audit.filter.action_placeholder")}
          placeholder={t("admin.audit.filter.action_placeholder")}
          value={filters.action ?? ""}
          onChange={(e) => setFilter("action", e.target.value)}
        />
        <label style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 6 }}>
          {t("admin.audit.filter.from")}
          <input type="date" value={filters.from ?? ""} onChange={(e) => setFilter("from", e.target.value)} />
        </label>
        <label style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 6 }}>
          {t("admin.audit.filter.to")}
          <input type="date" value={filters.to ?? ""} onChange={(e) => setFilter("to", e.target.value)} />
        </label>
        <AdminButton variant="secondary" disabled={exporting} onClick={handleExport}>
          {exporting ? t("admin.audit.export.loading") : t("admin.audit.export.csv")}
        </AdminButton>
      </div>
      {exportError && (
        <div role="alert" style={{ marginBottom: 12, color: "var(--text-tertiary)" }}>
          {exportError}
        </div>
      )}
      <AuditTimeline key={filterKey} filters={filters} />
    </div>
  );
}
