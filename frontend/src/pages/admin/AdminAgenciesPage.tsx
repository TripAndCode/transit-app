import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import {
  useAdminAgencies,
  useAgenciesHealth,
  useCreateAgencyAdmin,
  useDeleteAgency,
  usePatchAgency,
  useRestoreAgency,
  type AdminAgency,
  type AgencyHealthRow,
} from "../../api/admin";
import { formatApiError } from "../../api/client";
import { formatDateTime, EM_DASH } from "../../utils/format";
import { AdminButton, AdminSearchInput, StatusChip } from "./adminControls";
import { Modal } from "../../components/Modal";
import { PageHeader } from "../../components/ui/PageHeader";
import { DataTable, type DataTableColumn } from "../../components/admin/DataTable";
import { Drawer } from "../../components/admin/Drawer";
import { AgencyDiagnosticsDrawer } from "./AgencyDiagnosticsDrawer";
import { ClampSparkline } from "./ClampSparkline";

const STRATEGIES = ["aomori_regex", "direct_url", "aomori_index_scrape", "static_join"] as const;

/** URL search param backing the saved-view chips, so a view survives a reload
 *  and can be linked to. */
const VIEW_PARAM = "view";
const DEFAULT_VIEW = "all";

// ── Agency form modal ────────────────────────────────────────────────────

type FormState = {
  agency_name: string;
  feed_url: string;
  static_url: string;
  ingest_strategy: string;
  trip_id_pattern: string;
};

const EMPTY_FORM: FormState = {
  agency_name: "",
  feed_url: "",
  static_url: "",
  ingest_strategy: "",
  trip_id_pattern: "",
};

function agencyToForm(a: AdminAgency): FormState {
  return {
    agency_name: a.agency_name,
    feed_url: a.feed_url,
    static_url: a.static_url ?? "",
    ingest_strategy: a.ingest_strategy ?? "",
    trip_id_pattern: a.trip_id_pattern ?? "",
  };
}

function AgencyFormModal({
  initial,
  isEdit,
  onClose,
  onSubmit,
  isPending,
  error,
}: {
  initial: FormState;
  isEdit: boolean;
  onClose: () => void;
  onSubmit: (f: FormState) => void;
  isPending: boolean;
  error: unknown;
}) {
  const { t } = useTranslation();
  const [form, setForm] = useState<FormState>(initial);
  const nameInputRef = useRef<HTMLInputElement>(null);

  function set(key: keyof FormState, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  return (
    <Modal
      open
      onClose={onClose}
      labelledBy="agency-form-title"
      initialFocusRef={nameInputRef}
      style={{
        background: "var(--bg-surface)", padding: 24, borderRadius: "var(--radius-lg)",
        width: 520, maxWidth: "90vw",
      }}
    >
      <form
        onSubmit={(e) => { e.preventDefault(); onSubmit(form); }}
        style={{ display: "flex", flexDirection: "column", gap: 14 }}
      >
        <h3 id="agency-form-title" style={{ margin: 0, fontSize: 18 }}>
          {isEdit ? t("admin.agencies.form_title_edit") : t("admin.agencies.form_title_add")}
        </h3>
        <Field label={t("admin.agencies.form_name")} htmlFor="af-name">
          <input
            id="af-name"
            ref={nameInputRef}
            required
            value={form.agency_name}
            onChange={(e) => set("agency_name", e.target.value)}
            style={{ width: "100%" }}
          />
        </Field>
        <Field label={t("admin.agencies.form_feed_url")} htmlFor="af-feed">
          <input
            id="af-feed"
            type="url"
            required
            value={form.feed_url}
            onChange={(e) => set("feed_url", e.target.value)}
            style={{ width: "100%" }}
          />
          {form.feed_url && !form.feed_url.startsWith("http://") && !form.feed_url.startsWith("https://") && (
            <div style={{ color: "var(--color-warning-text)", fontSize: 12, marginTop: 2 }}>
              {t("admin.agencies.form_error_feed_url")}
            </div>
          )}
        </Field>
        <Field label={t("admin.agencies.form_static_url")} htmlFor="af-static">
          <input
            id="af-static"
            type="url"
            value={form.static_url}
            onChange={(e) => set("static_url", e.target.value)}
            style={{ width: "100%" }}
          />
        </Field>
        <Field label={t("admin.agencies.form_strategy")} htmlFor="af-strategy">
          <select
            id="af-strategy"
            value={form.ingest_strategy}
            onChange={(e) => set("ingest_strategy", e.target.value)}
            style={{ width: "100%" }}
          >
            <option value="">{t("admin.agencies.form_strategy_placeholder")}</option>
            {STRATEGIES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </Field>
        <Field label={t("admin.agencies.form_trip_pattern")} htmlFor="af-pattern">
          <input
            id="af-pattern"
            value={form.trip_id_pattern}
            onChange={(e) => set("trip_id_pattern", e.target.value)}
            style={{ width: "100%" }}
          />
        </Field>
        {!!error && (
          <div style={{ color: "var(--color-warning-text)", fontSize: 13 }}>
            {formatApiError(error)}
          </div>
        )}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
          <AdminButton variant="secondary" onClick={onClose}>
            {t("common.cancel")}
          </AdminButton>
          <AdminButton variant="primary" type="submit" disabled={isPending}>
            {isPending
              ? t("admin.agencies.form_submitting")
              : isEdit
              ? t("admin.agencies.form_submit_edit")
              : t("admin.agencies.form_submit_add")}
          </AdminButton>
        </div>
      </form>
    </Modal>
  );
}

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: React.ReactNode }) {
  return (
    <label htmlFor={htmlFor} style={{ display: "block" }}>
      <div style={{ marginBottom: 4, fontSize: 13, color: "var(--text-secondary)" }}>{label}</div>
      {children}
    </label>
  );
}

// ── Health cells ─────────────────────────────────────────────────────────

function FreshnessCell({ health }: { health?: AgencyHealthRow }) {
  const { t } = useTranslation();
  if (!health) return <span style={{ color: "var(--text-tertiary)" }}>{EM_DASH}</span>;
  const tone = health.freshness === "fresh" ? "good" : health.freshness === "stale" ? "warn" : "neutral";
  return <StatusChip tone={tone}>{t(`admin.agencies.freshness_${health.freshness}`)}</StatusChip>;
}

/** A whole-registry summary rather than a per-field list: the row has to stay
 *  scannable, and the field-by-field verdicts are one click away in the drawer. */
function RtCoverageCell({ health }: { health?: AgencyHealthRow }) {
  const { t } = useTranslation();
  if (!health) return <span style={{ color: "var(--text-tertiary)" }}>{EM_DASH}</span>;
  if (!health.rt_coverage.probed) {
    return <StatusChip tone="neutral">{t("admin.agencies.rt_unprobed")}</StatusChip>;
  }
  return (
    <StatusChip tone={health.rt_coverage.complete ? "good" : "warn"}>
      {t("admin.agencies.rt_summary", {
        present: health.rt_coverage.present_count,
        total: health.rt_coverage.field_count,
      })}
    </StatusChip>
  );
}


// ── Page ─────────────────────────────────────────────────────────────────

export function AdminAgenciesPage() {
  const { t } = useTranslation();
  const { data: agencies, isLoading, error } = useAdminAgencies();
  const { data: health, error: healthError } = useAgenciesHealth();
  const create = useCreateAgencyAdmin();
  const patch = usePatchAgency();
  const del = useDeleteAgency();
  const restore = useRestoreAgency();

  const [search, setSearch] = useState("");
  const view = useSearchParams()[0].get(VIEW_PARAM) ?? DEFAULT_VIEW;
  const [openedId, setOpenedId] = useState<number | null>(null);

  // null = closed; undefined = new; AdminAgency = editing
  const [editing, setEditing] = useState<AdminAgency | undefined | null>(null);

  const healthById = new Map((health ?? []).map((h) => [h.agency_id, h]));

  const filtered = (agencies ?? []).filter((a) => {
    if (!a.agency_name.toLowerCase().includes(search.trim().toLowerCase())) return false;
    const h = healthById.get(a.agency_id);
    if (view === "stale") return h?.freshness === "stale";
    if (view === "rt_incomplete") return h != null && (!h.rt_coverage.probed || !h.rt_coverage.complete);
    return true;
  });

  const staleCount = (health ?? []).filter((h) => h.freshness === "stale").length;
  const rtGapCount = (health ?? []).filter((h) => !h.rt_coverage.probed || !h.rt_coverage.complete).length;

  // Derived from the list rather than held as its own copy of the row: an
  // agency that leaves the list (filtered out, or disabled) must not leave a
  // drawer describing it open.
  const opened = filtered.find((a) => a.agency_id === openedId) ?? null;

  const columns: DataTableColumn<AdminAgency>[] = [
    {
      key: "name",
      header: t("admin.agencies.col_name"),
      render: (a) => (
        <span style={{ fontWeight: 500, opacity: a.deleted_at ? 0.55 : 1 }}>
          {a.agency_name}
          {a.deleted_at && (
            <span style={{ marginLeft: 8 }}>
              <StatusChip tone="neutral">{t("admin.agencies.status_deleted")}</StatusChip>
            </span>
          )}
        </span>
      ),
    },
    {
      key: "freshness",
      header: t("admin.agencies.col_freshness"),
      render: (a) => <FreshnessCell health={healthById.get(a.agency_id)} />,
    },
    {
      key: "last_capture",
      header: t("admin.agencies.col_last_capture"),
      render: (a) => (
        <span style={{ color: "var(--text-tertiary)", fontSize: 12 }}>
          {formatDateTime(healthById.get(a.agency_id)?.last_capture_at ?? "")}
        </span>
      ),
    },
    {
      key: "rt",
      header: t("admin.agencies.col_rt_coverage"),
      render: (a) => <RtCoverageCell health={healthById.get(a.agency_id)} />,
    },
    {
      key: "clamp",
      header: t("admin.agencies.col_clamp"),
      render: (a) => {
        const h = healthById.get(a.agency_id);
        return h ? (
          <ClampSparkline days={h.clamp_history} label={t("admin.agencies.col_clamp")} />
        ) : (
          <span style={{ color: "var(--text-tertiary)" }}>{EM_DASH}</span>
        );
      },
    },
    {
      key: "static",
      header: t("admin.agencies.col_static_version"),
      render: (a) => (
        <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
          {healthById.get(a.agency_id)?.static_version?.version ?? EM_DASH}
        </span>
      ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (a) => (
        <span style={{ whiteSpace: "nowrap" }} onClick={(e) => e.stopPropagation()} role="presentation">
          <AdminButton
            variant="secondary"
            onClick={() => {
              create.reset();
              patch.reset();
              setEditing(a);
            }}
          >
            {t("admin.agencies.action_edit")}
          </AdminButton>
        </span>
      ),
    },
  ];

  async function handleSubmit(form: FormState) {
    const body = {
      agency_name: form.agency_name,
      feed_url: form.feed_url,
      static_url: form.static_url || null,
      ingest_strategy: form.ingest_strategy || null,
      trip_id_pattern: form.trip_id_pattern || null,
    };
    try {
      if (editing === undefined) {
        await create.mutateAsync(body);
      } else if (editing) {
        await patch.mutateAsync({ id: editing.agency_id, body });
      }
      setEditing(null);
    } catch {
      // error shown in form via create.error / patch.error
    }
  }

  return (
    // `position: relative` and a floor on the height are what the drawer
    // anchors to: it is absolutely positioned inside this box so the list
    // behind it keeps its place and its scroll position.
    <div style={{ padding: 24, position: "relative", minHeight: 520 }}>
      <PageHeader
        title={t("admin.agencies.title")}
        actions={<>
          <AdminSearchInput
            placeholder={t("admin.agencies.search_placeholder")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <AdminButton
            variant="primary"
            onClick={() => {
              create.reset();
              patch.reset();
              setEditing(undefined);
            }}
          >
            {t("admin.agencies.add_button")}
          </AdminButton>
        </>}
      />

      {error && <div style={{ color: "var(--text-tertiary)", marginBottom: 12 }}>{formatApiError(error)}</div>}
      {!!healthError && (
        <div style={{ color: "var(--text-tertiary)", marginBottom: 12 }}>{t("admin.agencies.health_error")}</div>
      )}
      {isLoading && <div style={{ color: "var(--text-tertiary)" }}>{t("common.loading")}</div>}

      <DataTable
        columns={columns}
        rows={filtered}
        rowKey={(a) => String(a.agency_id)}
        rowLabel={(a) => a.agency_name}
        caption={t("admin.agencies.table_label")}
        emptyLabel={t("admin.agencies.empty")}
        onOpen={(a) => setOpenedId(a.agency_id)}
        activeRowKey={openedId == null ? null : String(openedId)}
        savedViews={[
          { id: DEFAULT_VIEW, label: t("admin.agencies.view_all") },
          { id: "stale", label: t("admin.agencies.view_stale"), count: staleCount },
          { id: "rt_incomplete", label: t("admin.agencies.view_rt_incomplete"), count: rtGapCount },
        ]}
        savedViewParam={VIEW_PARAM}
      />

      <Drawer
        open={opened != null}
        onClose={() => setOpenedId(null)}
        label={t("admin.agency_diag.drawer_label")}
      >
        {opened && (
          <AgencyDiagnosticsDrawer
            agency={opened}
            onClose={() => setOpenedId(null)}
            disablePending={del.isPending && del.variables === opened.agency_id}
            onDisable={(id) => {
              del.mutate(id);
              setOpenedId(null);
            }}
            onRestore={(id) => {
              restore.mutate(id);
              setOpenedId(null);
            }}
          />
        )}
      </Drawer>

      {editing !== null && (
        <AgencyFormModal
          initial={editing === undefined ? EMPTY_FORM : agencyToForm(editing)}
          isEdit={editing !== undefined}
          onClose={() => {
            create.reset();
            patch.reset();
            setEditing(null);
          }}
          onSubmit={handleSubmit}
          isPending={create.isPending || patch.isPending}
          error={create.error || patch.error}
        />
      )}
    </div>
  );
}
