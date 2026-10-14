import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import {
  useAdminAgencies,
  useAgenciesHealth,
  useCreateAgencyAdmin,
  useDeleteAgency,
  usePatchAgency,
  useReanalyzeAgency,
  useRefreshAgencyStatic,
  useRestoreAgency,
  useUpdateAgency,
  type AdminAgency,
  type AgencyHealthRow,
} from "../../api/admin";
import { formatApiError } from "../../api/client";
import { ErrorBanner } from "../../components/ErrorBanner";
import { formatDateTime, EM_DASH } from "../../utils/format";
import { AdminButton, AdminSearchInput, StatusChip } from "./adminControls";
import { Modal } from "../../components/Modal";
import { PageHeader } from "../../components/ui/PageHeader";
import { DataTable, type DataTableColumn } from "../../components/admin/DataTable";
import { Drawer } from "../../components/admin/Drawer";
import { AgencyDiagnosticsDrawer } from "./AgencyDiagnosticsDrawer";
import { AgencyRunCard, DataStage, RunChain, TimetableStage } from "./AgencyRuns";
import { ClampSparkline } from "./ClampSparkline";

const STRATEGIES = ["aomori_regex", "direct_url", "aomori_index_scrape", "static_join"] as const;

/** The strategies that can fetch a timetable; mirrors the server's allow-list. */
const STATIC_STRATEGIES = ["direct_url", "aomori_index_scrape"] as const;

/** The fetch method a newly entered timetable URL starts with: the one every
 *  plain zip URL uses. */
const DEFAULT_STATIC_STRATEGY = "direct_url";

/** URL search param backing the saved-view chips, so a view survives a reload
 *  and can be linked to. */
const VIEW_PARAM = "view";
const DEFAULT_VIEW = "all";

// ── Agency form modal ────────────────────────────────────────────────────

type FormState = {
  agency_name: string;
  feed_url: string;
  static_url: string;
  static_strategy: string;
  ingest_strategy: string;
  trip_id_pattern: string;
};

const EMPTY_FORM: FormState = {
  agency_name: "",
  feed_url: "",
  static_url: "",
  static_strategy: "",
  ingest_strategy: "",
  trip_id_pattern: "",
};

function agencyToForm(a: AdminAgency): FormState {
  return {
    agency_name: a.agency_name,
    feed_url: a.feed_url,
    static_url: a.static_url ?? "",
    static_strategy: a.static_strategy ?? "",
    ingest_strategy: a.ingest_strategy ?? "",
    trip_id_pattern: a.trip_id_pattern ?? "",
  };
}

/** What the form asks for after saving: nothing more, or the chained update
 *  that takes a new agency from saved to aggregated. */
type SubmitIntent = { setup: boolean };

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
  onSubmit: (f: FormState, intent: SubmitIntent) => void;
  isPending: boolean;
  error: unknown;
}) {
  const { t } = useTranslation();
  const [form, setForm] = useState<FormState>(initial);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const canSetUp = form.static_url.trim() !== "" && form.static_strategy !== "";

  function set(key: keyof FormState, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function setStaticUrl(value: string) {
    setForm((f) => ({
      ...f,
      static_url: value,
      static_strategy: f.static_strategy === "" && value.trim() !== "" ? DEFAULT_STATIC_STRATEGY : f.static_strategy,
    }));
  }

  function saveAndSetUp() {
    // A type="button" press skips the browser's own `required`/`type=url`
    // checks, which a submit would have run first.
    if (!formRef.current?.reportValidity()) return;
    onSubmit(form, { setup: true });
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
        ref={formRef}
        onSubmit={(e) => { e.preventDefault(); onSubmit(form, { setup: false }); }}
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
            onChange={(e) => setStaticUrl(e.target.value)}
            style={{ width: "100%" }}
          />
        </Field>
        <Field label={t("admin.agencies.form_static_strategy")} htmlFor="af-static-strategy">
          <select
            id="af-static-strategy"
            value={form.static_strategy}
            onChange={(e) => set("static_strategy", e.target.value)}
            style={{ width: "100%" }}
          >
            <option value="">{t("admin.agencies.form_static_strategy_none")}</option>
            {STATIC_STRATEGIES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
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
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
          <AdminButton variant="secondary" onClick={onClose}>
            {t("common.cancel")}
          </AdminButton>
          {isEdit ? (
            <AdminButton variant="primary" type="submit" disabled={isPending}>
              {isPending ? t("admin.agencies.form_submitting") : t("admin.agencies.form_submit_edit")}
            </AdminButton>
          ) : (
            <>
              <AdminButton variant="secondary" type="submit" disabled={isPending}>
                {isPending ? t("admin.agencies.form_submitting") : t("admin.agencies.form_save_only")}
              </AdminButton>
              <AdminButton
                variant="primary"
                disabled={isPending || !canSetUp}
                title={canSetUp ? undefined : t("admin.agencies.form_setup_needs_static")}
                onClick={saveAndSetUp}
              >
                {t("admin.agencies.form_save_setup")}
              </AdminButton>
            </>
          )}
        </div>
        {!isEdit && (
          <div style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>
            {canSetUp ? t("admin.agencies.form_setup_note") : t("admin.agencies.form_setup_needs_static")}
          </div>
        )}
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
  const update = useUpdateAgency();
  const refreshStatic = useRefreshAgencyStatic();
  const reanalyze = useReanalyzeAgency();

  const [search, setSearch] = useState("");
  const view = useSearchParams()[0].get(VIEW_PARAM) ?? DEFAULT_VIEW;
  const [openedId, setOpenedId] = useState<number | null>(null);

  // null = closed; undefined = new; AdminAgency = editing
  const [editing, setEditing] = useState<AdminAgency | undefined | null>(null);

  /** The name a mutation was run for; the id stands in if the row is gone. */
  const agencyLabel = (id: number | undefined) =>
    agencies?.find((a) => a.agency_id === id)?.agency_name ?? `#${id}`;

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
  const openedHealth = opened ? healthById.get(opened.agency_id) : undefined;

  const runMutations = [update, refreshStatic, reanalyze];
  const startFailure = runMutations.find((m) => m.error != null);

  /** A job is working on this agency, or a run is being started for it.
   *  `in_flight` is the server's own rule, so a run row a dead process left
   *  `running` stops blocking the buttons when it stops blocking a press. */
  function isBusy(a: AdminAgency): boolean {
    if (healthById.get(a.agency_id)?.in_flight) return true;
    return runMutations.some((m) => m.isPending && m.variables === a.agency_id);
  }

  /** Why a run cannot start for this agency right now, or null when it can.
   *  `needsStatic`: the run begins with a timetable reload. */
  function runBlocker(a: AdminAgency, needsStatic: boolean): string | null {
    if (a.deleted_at != null) return t("admin.agency_run.disabled_hint");
    if (isBusy(a)) return t("admin.agency_run.busy_hint");
    if (needsStatic && !(a.static_url && a.static_strategy)) return t("admin.agency_run.no_static_hint");
    return null;
  }

  /** Whether a run of this kind starts with a timetable reload. */
  const reloadsTimetable = (kind: string) => kind === "update" || kind === "static";

  /** Run again what the agency's latest run was. */
  function retry(a: AdminAgency, kind: string) {
    if (kind === "update") update.mutate(a.agency_id);
    else if (kind === "static") refreshStatic.mutate(a.agency_id);
    else reanalyze.mutate(a.agency_id);
  }

  function openEditor(a: AdminAgency) {
    create.reset();
    patch.reset();
    setEditing(a);
  }

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
      key: "timetable",
      header: t("admin.agency_run.col_timetable"),
      render: (a) => {
        const h = healthById.get(a.agency_id);
        // A run in flight takes this cell over with its step chain: the
        // buttons it replaces could not start anything until it ends.
        if (h?.in_flight && h.latest_run?.status === "running") return <RunChain run={h.latest_run} />;
        return (
          <span onClick={(e) => e.stopPropagation()} role="presentation">
            <TimetableStage
              health={h}
              blocker={runBlocker(a, true)}
              onRun={() => refreshStatic.mutate(a.agency_id)}
            />
          </span>
        );
      },
    },
    {
      key: "data",
      header: t("admin.agency_run.col_data"),
      render: (a) => (
        <span onClick={(e) => e.stopPropagation()} role="presentation">
          <DataStage
            health={healthById.get(a.agency_id)}
            blocker={runBlocker(a, false)}
            onRun={() => reanalyze.mutate(a.agency_id)}
          />
        </span>
      ),
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
      key: "actions",
      header: "",
      align: "right",
      render: (a) => {
        const blocker = runBlocker(a, true);
        return (
          <span className="agency-row-actions" onClick={(e) => e.stopPropagation()} role="presentation">
            <AdminButton
              variant="primary"
              disabled={blocker != null}
              title={blocker ?? undefined}
              onClick={() => update.mutate(a.agency_id)}
            >
              {isBusy(a) ? t("admin.agency_run.running") : t("admin.agency_run.update_all")}
            </AdminButton>
            <AdminButton
              variant="secondary"
              disabled={a.deleted_at != null}
              title={a.deleted_at == null ? undefined : t("admin.agencies.action_edit_disabled_hint")}
              onClick={() => openEditor(a)}
            >
              {t("admin.agencies.action_edit")}
            </AdminButton>
          </span>
        );
      },
    },
  ];

  async function handleSubmit(form: FormState, { setup }: SubmitIntent) {
    const body = {
      agency_name: form.agency_name,
      feed_url: form.feed_url,
      static_url: form.static_url || null,
      static_strategy: form.static_strategy || null,
      ingest_strategy: form.ingest_strategy || null,
      trip_id_pattern: form.trip_id_pattern || null,
    };
    try {
      if (editing === undefined) {
        const created = await create.mutateAsync(body);
        if (setup) update.mutate(created.agency_id);
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
      {/* The drawer closes as soon as disable/restore is confirmed, so a
          failed mutation has to surface here or it is never seen. */}
      {del.error != null && (
        <ErrorBanner
          error={del.error}
          prefix={t("admin.agencies.disable_failed", { name: agencyLabel(del.variables) })}
        />
      )}
      {restore.error != null && (
        <ErrorBanner
          error={restore.error}
          prefix={t("admin.agencies.restore_failed", { name: agencyLabel(restore.variables) })}
        />
      )}
      {startFailure != null && (
        <ErrorBanner
          error={startFailure.error}
          prefix={t("admin.agency_run.start_failed", { name: agencyLabel(startFailure.variables) })}
        />
      )}
      {isLoading && <div style={{ color: "var(--text-tertiary)" }}>{t("common.loading")}</div>}

      <DataTable
        columns={columns}
        rows={filtered}
        rowKey={(a) => String(a.agency_id)}
        rowLabel={(a) => a.agency_name}
        caption={t("admin.agencies.table_label")}
        emptyLabel={t("admin.agencies.empty")}
        settled={agencies != null}
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
            busy={runBlocker(opened, false) != null}
            runFinishedAt={openedHealth?.latest_run?.finished_at ?? null}
            runCard={
              openedHealth?.latest_run && (
                <AgencyRunCard
                  agencyId={opened.agency_id}
                  agencyName={opened.agency_name}
                  run={openedHealth.latest_run}
                  latestDataDate={openedHealth.latest_data_date}
                  retryBlocked={runBlocker(opened, reloadsTimetable(openedHealth.latest_run.kind)) != null}
                  onRetry={() => retry(opened, openedHealth.latest_run?.kind ?? "")}
                  onEditUrl={() => {
                    setOpenedId(null);
                    openEditor(opened);
                  }}
                />
              )
            }
            onClose={() => setOpenedId(null)}
            disablePending={del.isPending && del.variables === opened.agency_id}
            onDisable={(id) => {
              // One failure banner at a time: starting either action drops
              // the other's earlier failure (the same action resets itself).
              restore.reset();
              del.mutate(id);
              setOpenedId(null);
            }}
            onRestore={(id) => {
              del.reset();
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
