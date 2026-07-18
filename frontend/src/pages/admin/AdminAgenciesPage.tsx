import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AdminAgency,
  useAdminAgencies,
  useCreateAgencyAdmin,
  useDeleteAgency,
  usePatchAgency,
  useRestoreAgency,
} from "../../api/admin";
import { formatApiError } from "../../api/client";
import { AdminButton, StatusChip } from "./adminControls";

const STRATEGIES = ["aomori_regex", "direct_url", "aomori_index_scrape", "static_join"] as const;

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

  function set(key: keyof FormState, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  return (
    <div
      role="presentation"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.3)",
        zIndex: 200, display: "flex", alignItems: "center", justifyContent: "center",
      }}
    >
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="agency-form-title"
        onSubmit={(e) => { e.preventDefault(); onSubmit(form); }}
        style={{
          background: "var(--bg-surface)", padding: 24, borderRadius: "var(--radius-lg)",
          width: 520, maxWidth: "90vw", display: "flex", flexDirection: "column", gap: 14,
        }}
      >
        <h3 id="agency-form-title" style={{ margin: 0, fontSize: 18 }}>
          {isEdit ? t("admin.agencies.form_title_edit") : t("admin.agencies.form_title_add")}
        </h3>
        <Field label={t("admin.agencies.form_name")} htmlFor="af-name">
          <input
            id="af-name"
            // eslint-disable-next-line jsx-a11y/no-autofocus
            autoFocus
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
            <div style={{ color: "var(--color-warning)", fontSize: 12, marginTop: 2 }}>
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
          <div style={{ color: "var(--color-warning)", fontSize: 13 }}>
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
    </div>
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

// ── Page ─────────────────────────────────────────────────────────────────

export function AdminAgenciesPage() {
  const { t } = useTranslation();
  const { data: agencies, isLoading, error } = useAdminAgencies();
  const create = useCreateAgencyAdmin();
  const patch = usePatchAgency();
  const del = useDeleteAgency();
  const restore = useRestoreAgency();

  // null = closed; undefined = new; AdminAgency = editing
  const [editing, setEditing] = useState<AdminAgency | undefined | null>(null);

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
    <div style={{ padding: 24 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, margin: 0 }}>{t("admin.agencies.title")}</h1>
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
      </div>

      {error && <div style={{ color: "var(--text-tertiary)", marginBottom: 12 }}>{formatApiError(error)}</div>}
      {isLoading && <div style={{ color: "var(--text-tertiary)" }}>{t("common.loading")}</div>}

      <table className="admin-table">
        <thead>
          <tr>
            <th>{t("admin.agencies.col_name")}</th>
            <th>{t("admin.agencies.col_feed_url")}</th>
            <th>{t("admin.agencies.col_strategy")}</th>
            <th>{t("admin.agencies.col_status")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {agencies?.map((a) => (
            <tr key={a.agency_id} style={{ opacity: a.deleted_at ? 0.5 : 1 }}>
              <td style={{ fontWeight: 500 }}>{a.agency_name}</td>
              <td style={{ fontSize: 12, color: "var(--text-tertiary)", maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {a.feed_url}
              </td>
              <td>
                {a.ingest_strategy ? (
                  <StatusChip tone="neutral">{a.ingest_strategy}</StatusChip>
                ) : (
                  <span style={{ color: "var(--text-tertiary)", fontSize: 12 }}>—</span>
                )}
              </td>
              <td>
                <StatusChip tone={a.deleted_at ? "neutral" : "good"}>
                  {a.deleted_at ? t("admin.agencies.status_deleted") : t("admin.agencies.status_active")}
                </StatusChip>
              </td>
              <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                {!a.deleted_at && (
                  <>
                    <AdminButton
                      variant="secondary"
                      onClick={() => {
                        create.reset();
                        patch.reset();
                        setEditing(a);
                      }}
                      style={{ marginRight: 8 }}
                    >
                      {t("admin.agencies.action_edit")}
                    </AdminButton>
                    <AdminButton
                      variant="danger"
                      disabled={del.isPending && del.variables === a.agency_id}
                      onClick={() => {
                        if (confirm(t("admin.agencies.confirm_delete", { name: a.agency_name }))) {
                          del.mutate(a.agency_id);
                        }
                      }}
                    >
                      {t("admin.agencies.action_delete")}
                    </AdminButton>
                  </>
                )}
                {a.deleted_at && (
                  <AdminButton
                    variant="secondary"
                    disabled={restore.isPending && restore.variables === a.agency_id}
                    onClick={() => restore.mutate(a.agency_id)}
                  >
                    {t("admin.agencies.action_restore")}
                  </AdminButton>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

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
