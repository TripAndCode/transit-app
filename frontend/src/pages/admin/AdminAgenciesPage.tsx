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
          <button
            type="button"
            onClick={onClose}
            style={{
              background: "transparent", border: "1px solid var(--border-subtle)",
              padding: "6px 14px", borderRadius: 4,
            }}
          >
            {t("common.cancel")}
          </button>
          <button
            type="submit"
            disabled={isPending}
            style={{
              background: "var(--accent)", color: "#fff", border: "none",
              padding: "6px 16px", borderRadius: 4,
            }}
          >
            {isPending
              ? t("admin.agencies.form_submitting")
              : isEdit
              ? t("admin.agencies.form_submit_edit")
              : t("admin.agencies.form_submit_add")}
          </button>
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
        <button
          type="button"
          onClick={() => setEditing(undefined)}
          style={{
            background: "var(--accent)", color: "#fff", border: "none",
            padding: "6px 14px", borderRadius: 4, cursor: "pointer",
          }}
        >
          {t("admin.agencies.add_button")}
        </button>
      </div>

      {error && <div style={{ color: "var(--text-tertiary)", marginBottom: 12 }}>{formatApiError(error)}</div>}
      {isLoading && <div style={{ color: "var(--text-tertiary)" }}>{t("common.loading")}</div>}

      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
        <thead>
          <tr style={{ background: "var(--surface-1)" }}>
            <th style={{ padding: "8px 12px", textAlign: "left" }}>{t("admin.agencies.col_name")}</th>
            <th style={{ padding: "8px 12px", textAlign: "left" }}>{t("admin.agencies.col_feed_url")}</th>
            <th style={{ padding: "8px 12px", textAlign: "left" }}>{t("admin.agencies.col_strategy")}</th>
            <th style={{ padding: "8px 12px", textAlign: "left" }}>{t("admin.agencies.col_status")}</th>
            <th style={{ padding: "8px 12px" }} />
          </tr>
        </thead>
        <tbody>
          {agencies?.map((a) => (
            <tr
              key={a.agency_id}
              style={{
                borderBottom: "1px solid var(--surface-2)",
                opacity: a.deleted_at ? 0.5 : 1,
              }}
            >
              <td style={{ padding: "8px 12px" }}>{a.agency_name}</td>
              <td style={{ padding: "8px 12px", fontSize: 12, color: "var(--text-tertiary)", maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {a.feed_url}
              </td>
              <td style={{ padding: "8px 12px", color: "var(--text-tertiary)", fontSize: 12 }}>
                {a.ingest_strategy ?? "—"}
              </td>
              <td style={{ padding: "8px 12px" }}>
                <span
                  style={{
                    fontSize: 11, padding: "2px 8px", borderRadius: 4,
                    background: a.deleted_at ? "var(--surface-2)" : "var(--accent-soft)",
                    color: a.deleted_at ? "var(--text-tertiary)" : "var(--accent)",
                  }}
                >
                  {a.deleted_at ? t("admin.agencies.status_deleted") : t("admin.agencies.status_active")}
                </span>
              </td>
              <td style={{ padding: "8px 12px", textAlign: "right", whiteSpace: "nowrap" }}>
                {!a.deleted_at && (
                  <>
                    <button
                      type="button"
                      onClick={() => setEditing(a)}
                      style={{ marginRight: 8, fontSize: 13 }}
                    >
                      {t("admin.agencies.action_edit")}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        if (confirm(t("admin.agencies.confirm_delete", { name: a.agency_name }))) {
                          del.mutate(a.agency_id);
                        }
                      }}
                      style={{ fontSize: 13 }}
                    >
                      {t("admin.agencies.action_delete")}
                    </button>
                  </>
                )}
                {a.deleted_at && (
                  <button
                    type="button"
                    onClick={() => restore.mutate(a.agency_id)}
                    style={{ fontSize: 13 }}
                  >
                    {t("admin.agencies.action_restore")}
                  </button>
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
          onClose={() => setEditing(null)}
          onSubmit={handleSubmit}
          isPending={create.isPending || patch.isPending}
          error={create.error || patch.error}
        />
      )}
    </div>
  );
}
