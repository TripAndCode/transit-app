import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  useAgencyDiagnostics,
  usePatchAgencyStandards,
  usePatchAgencyWeights,
  useProbeAgencyFeed,
  useReanalyzeAgency,
  type AdminAgency,
  type AgencyStandard,
  type AgencyWeight,
  type RtFieldCoverage,
} from "../../api/admin";
import { formatApiError } from "../../api/client";
import { formatDateTime } from "../../utils/format";
import { AdminButton, StatusChip } from "./adminControls";
import { ClampSparkline } from "./ClampSparkline";

const METRIC_TYPES = ["ewt_sec", "vehicle_km_delivered_pct"] as const;

const EM_DASH = "—";

function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        fontSize: "var(--text-xs)",
        letterSpacing: "0.1em",
        textTransform: "uppercase",
        color: "var(--text-tertiary)",
        fontWeight: 600,
        marginTop: 6,
      }}
    >
      {children}
    </div>
  );
}

function KeyValue({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <span style={{ color: "var(--text-tertiary)" }}>{label}</span>
      <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{children}</span>
    </>
  );
}

function KeyValueGrid({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "104px minmax(0, 1fr)",
        gap: "6px 10px",
        fontSize: 12.5,
        alignItems: "baseline",
      }}
    >
      {children}
    </div>
  );
}


/** Four distinct verdicts, not two: a field nobody has measured must not read
 *  the same as one a probe refuted, and an expired verdict is "unknown again",
 *  not "absent". */
function rtTone(field: RtFieldCoverage): { tone: "good" | "warn" | "neutral"; key: string } {
  if (field.present) return { tone: "good", key: "rt_present" };
  if (!field.probed) return { tone: "neutral", key: "rt_unprobed" };
  if (field.expired) return { tone: "warn", key: "rt_expired" };
  return { tone: "warn", key: "rt_absent" };
}

// ── typed confirm ────────────────────────────────────────────────────────

/** Gates a destructive action behind retyping the target's own name.
 *
 *  Compared after trimming but WITH case: an operator who typed the name is
 *  looking at the row they mean, while a case-folded match would accept a
 *  half-remembered one.
 */
function TypedConfirm({
  expected,
  hint,
  inputLabel,
  confirmLabel,
  onConfirm,
  pending = false,
}: {
  expected: string;
  hint: string;
  inputLabel: string;
  confirmLabel: string;
  onConfirm: () => void;
  pending?: boolean;
}) {
  const [value, setValue] = useState("");
  const matches = value.trim() === expected;
  return (
    <div
      style={{
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-lg)",
        padding: "10px 12px",
        display: "grid",
        gap: 8,
        fontSize: 12.5,
      }}
    >
      <span style={{ color: "var(--text-secondary)" }}>{hint}</span>
      <label style={{ display: "block" }}>
        <span style={{ display: "block", marginBottom: 4, color: "var(--text-tertiary)", fontSize: 12 }}>
          {inputLabel}
        </span>
        <input
          value={value}
          placeholder={expected}
          onChange={(e) => setValue(e.target.value)}
          style={{ width: "100%" }}
        />
      </label>
      <div>
        <AdminButton variant="danger" disabled={!matches || pending} onClick={onConfirm}>
          {confirmLabel}
        </AdminButton>
      </div>
    </div>
  );
}

// ── editors ──────────────────────────────────────────────────────────────

type StandardDraft = { route_code: string; metric_type: string; threshold_value: string; bonus_malus_rate: string };
type WeightDraft = { route_code: string | null; weight: string };

function EditorShell({
  children,
  onSave,
  onCancel,
  saveDisabled,
  error,
}: {
  children: ReactNode;
  onSave: () => void;
  onCancel: () => void;
  saveDisabled: boolean;
  error: unknown;
}) {
  const { t } = useTranslation();
  return (
    <div style={{ display: "grid", gap: 8, fontSize: 12.5 }}>
      {children}
      {!!error && <div style={{ color: "var(--color-warning)" }}>{formatApiError(error)}</div>}
      <div style={{ display: "flex", gap: 8 }}>
        <AdminButton variant="primary" disabled={saveDisabled} onClick={onSave}>
          {t("admin.agency_diag.editor_save")}
        </AdminButton>
        <AdminButton variant="secondary" onClick={onCancel}>
          {t("common.cancel")}
        </AdminButton>
      </div>
    </div>
  );
}

function isPositiveNumber(raw: string): boolean {
  const n = Number(raw);
  return raw.trim() !== "" && Number.isFinite(n) && n > 0;
}

function isNonNegativeNumber(raw: string): boolean {
  const n = Number(raw);
  return raw.trim() !== "" && Number.isFinite(n) && n >= 0;
}

function StandardsEditor({
  agencyId,
  rows,
  onDone,
}: {
  agencyId: number;
  rows: AgencyStandard[];
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const patch = usePatchAgencyStandards();
  const [drafts, setDrafts] = useState<StandardDraft[]>(() =>
    rows.map((r) => ({
      route_code: r.route_code,
      metric_type: r.metric_type,
      threshold_value: String(r.threshold_value),
      bonus_malus_rate: String(r.bonus_malus_rate),
    }))
  );
  const [removed, setRemoved] = useState<AgencyStandard[]>([]);

  function update(index: number, key: keyof StandardDraft, value: string) {
    setDrafts((ds) => ds.map((d, i) => (i === index ? { ...d, [key]: value } : d)));
  }

  function remove(index: number) {
    const draft = drafts[index];
    const original = rows.find((r) => r.route_code === draft.route_code && r.metric_type === draft.metric_type);
    if (original) setRemoved((rs) => [...rs, original]);
    setDrafts((ds) => ds.filter((_, i) => i !== index));
  }

  const valid = drafts.every(
    (d) =>
      d.route_code.trim() !== "" &&
      Number.isFinite(Number(d.threshold_value)) &&
      d.threshold_value.trim() !== "" &&
      isNonNegativeNumber(d.bonus_malus_rate)
  );

  async function save() {
    try {
      await patch.mutateAsync({
        id: agencyId,
        body: {
          upsert: drafts.map((d) => ({
            route_code: d.route_code.trim(),
            metric_type: d.metric_type,
            threshold_value: Number(d.threshold_value),
            bonus_malus_rate: Number(d.bonus_malus_rate),
          })),
          delete: removed,
        },
      });
      onDone();
    } catch {
      // surfaced via patch.error below
    }
  }

  return (
    <EditorShell onSave={save} onCancel={onDone} saveDisabled={!valid || patch.isPending} error={patch.error}>
      {drafts.length === 0 && (
        <span style={{ color: "var(--text-tertiary)" }}>{t("admin.agency_diag.editor_empty")}</span>
      )}
      {drafts.map((d, i) => (
        <div
          key={`${d.route_code}-${d.metric_type}-${i}`}
          style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 6, alignItems: "end" }}
        >
          <label>
            <span style={{ display: "block", color: "var(--text-tertiary)", fontSize: "var(--text-xs)" }}>
              {t("admin.agency_diag.editor_route")}
            </span>
            <input
              value={d.route_code}
              onChange={(e) => update(i, "route_code", e.target.value)}
              style={{ width: "100%" }}
            />
          </label>
          <label>
            <span style={{ display: "block", color: "var(--text-tertiary)", fontSize: "var(--text-xs)" }}>
              {t("admin.agency_diag.editor_metric")}
            </span>
            <select
              value={d.metric_type}
              onChange={(e) => update(i, "metric_type", e.target.value)}
              style={{ width: "100%" }}
            >
              {METRIC_TYPES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
          <AdminButton
            variant="secondary"
            aria-label={t("admin.agency_diag.editor_remove")}
            onClick={() => remove(i)}
          >
            ×
          </AdminButton>
          <label>
            <span style={{ display: "block", color: "var(--text-tertiary)", fontSize: "var(--text-xs)" }}>
              {t("admin.agency_diag.editor_threshold")}
            </span>
            <input
              type="number"
              value={d.threshold_value}
              onChange={(e) => update(i, "threshold_value", e.target.value)}
              style={{ width: "100%" }}
            />
          </label>
          <label>
            <span style={{ display: "block", color: "var(--text-tertiary)", fontSize: "var(--text-xs)" }}>
              {t("admin.agency_diag.editor_rate")}
            </span>
            <input
              type="number"
              value={d.bonus_malus_rate}
              onChange={(e) => update(i, "bonus_malus_rate", e.target.value)}
              style={{ width: "100%" }}
            />
          </label>
        </div>
      ))}
      <div>
        <AdminButton
          variant="secondary"
          onClick={() =>
            setDrafts((ds) => [
              ...ds,
              { route_code: "", metric_type: METRIC_TYPES[0], threshold_value: "0", bonus_malus_rate: "0" },
            ])
          }
        >
          {t("admin.agency_diag.editor_add")}
        </AdminButton>
      </div>
    </EditorShell>
  );
}

function WeightsEditor({
  agencyId,
  rows,
  onDone,
}: {
  agencyId: number;
  rows: AgencyWeight[];
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const patch = usePatchAgencyWeights();
  const [drafts, setDrafts] = useState<WeightDraft[]>(() =>
    rows.map((r) => ({ route_code: r.route_code, weight: String(r.weight) }))
  );
  const [removed, setRemoved] = useState<AgencyWeight[]>([]);

  function remove(index: number) {
    const draft = drafts[index];
    const original = rows.find((r) => r.route_code === draft.route_code);
    if (original) setRemoved((rs) => [...rs, original]);
    setDrafts((ds) => ds.filter((_, i) => i !== index));
  }

  const valid = drafts.every((d) => isPositiveNumber(d.weight) && (d.route_code === null || d.route_code.trim() !== ""));

  async function save() {
    try {
      await patch.mutateAsync({
        id: agencyId,
        body: {
          upsert: drafts.map((d) => ({
            route_code: d.route_code === null ? null : d.route_code.trim(),
            weight: Number(d.weight),
          })),
          delete: removed,
        },
      });
      onDone();
    } catch {
      // surfaced via patch.error below
    }
  }

  return (
    <EditorShell onSave={save} onCancel={onDone} saveDisabled={!valid || patch.isPending} error={patch.error}>
      {drafts.length === 0 && (
        <span style={{ color: "var(--text-tertiary)" }}>{t("admin.agency_diag.editor_empty")}</span>
      )}
      {drafts.map((d, i) => (
        <div
          key={`${d.route_code ?? "__default__"}-${i}`}
          style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 6, alignItems: "end" }}
        >
          <label>
            <span style={{ display: "block", color: "var(--text-tertiary)", fontSize: "var(--text-xs)" }}>
              {t("admin.agency_diag.editor_route")}
            </span>
            {d.route_code === null ? (
              <span style={{ display: "block", padding: "4px 0", color: "var(--text-secondary)" }}>
                {t("admin.agency_diag.editor_default_route")}
              </span>
            ) : (
              <input
                value={d.route_code}
                onChange={(e) =>
                  setDrafts((ds) => ds.map((x, j) => (j === i ? { ...x, route_code: e.target.value } : x)))
                }
                style={{ width: "100%" }}
              />
            )}
          </label>
          <label>
            <span style={{ display: "block", color: "var(--text-tertiary)", fontSize: "var(--text-xs)" }}>
              {t("admin.agency_diag.editor_weight")}
            </span>
            <input
              type="number"
              value={d.weight}
              onChange={(e) => setDrafts((ds) => ds.map((x, j) => (j === i ? { ...x, weight: e.target.value } : x)))}
              style={{ width: "100%" }}
            />
          </label>
          <AdminButton
            variant="secondary"
            aria-label={t("admin.agency_diag.editor_remove")}
            onClick={() => remove(i)}
          >
            ×
          </AdminButton>
        </div>
      ))}
      <div>
        <AdminButton
          variant="secondary"
          onClick={() => setDrafts((ds) => [...ds, { route_code: "", weight: "1" }])}
        >
          {t("admin.agency_diag.editor_add")}
        </AdminButton>
      </div>
    </EditorShell>
  );
}

// ── drawer body ──────────────────────────────────────────────────────────

/** Feed health, RT field diagnostics, static version history, the two manual
 *  policy tables, the per-agency actions, and the danger zone.
 *
 *  Rendered inside the list page's `Drawer`, which owns the panel chrome and
 *  focus handling; this component is only the content.
 */
export function AgencyDiagnosticsDrawer({
  agency,
  onClose,
  onDisable,
  onRestore,
  disablePending = false,
  staticReloadAvailable = false,
}: {
  agency: AdminAgency;
  onClose: () => void;
  onDisable: (id: number) => void;
  onRestore: (id: number) => void;
  disablePending?: boolean;
  staticReloadAvailable?: boolean;
}) {
  const { t } = useTranslation();
  const { data, isLoading, error } = useAgencyDiagnostics(agency.agency_id);
  const probe = useProbeAgencyFeed();
  const reanalyze = useReanalyzeAgency();
  const [editing, setEditing] = useState<"standards" | "weights" | null>(null);

  const freshnessLabel = data ? t(`admin.agencies.freshness_${data.freshness}`) : EM_DASH;

  return (
    <>
      <h4 style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, margin: 0 }}>
        <span style={{ fontSize: 15, fontWeight: 700, minWidth: 0, overflowWrap: "anywhere" }}>
          {agency.agency_name}
        </span>
        <AdminButton variant="secondary" onClick={onClose}>
          {t("common.close")}
        </AdminButton>
      </h4>

      {isLoading && <div style={{ color: "var(--text-tertiary)" }}>{t("common.loading")}</div>}
      {!!error && <div style={{ color: "var(--color-warning)", fontSize: 13 }}>{formatApiError(error)}</div>}

      {data && (
        <>
          <KeyValueGrid>
            <KeyValue label={t("admin.agency_diag.feed")}>
              <span style={{ fontSize: 12 }}>{data.feed_url}</span>
            </KeyValue>
            <KeyValue label={t("admin.agency_diag.strategy")}>
              {data.ingest_strategy ?? EM_DASH}
            </KeyValue>
            <KeyValue label={t("admin.agency_diag.last_capture")}>
              {formatDateTime(data.last_capture_at ?? "")}
            </KeyValue>
            <KeyValue label={t("admin.agency_diag.aggregation")}>
              <StatusChip tone={data.freshness === "fresh" ? "good" : data.freshness === "stale" ? "warn" : "neutral"}>
                {freshnessLabel}
              </StatusChip>{" "}
              <span style={{ color: "var(--text-tertiary)" }}>{data.latest_data_date ?? EM_DASH}</span>
            </KeyValue>
            <KeyValue label={t("admin.agency_diag.clamp")}>
              <ClampSparkline days={data.clamp_history} label={t("admin.agency_diag.clamp")} />
            </KeyValue>
          </KeyValueGrid>

          <SectionTitle>{t("admin.agency_diag.rt_section")}</SectionTitle>
          <div style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>
            {t("admin.agency_diag.rt_scope_note")}
          </div>
          <KeyValueGrid>
            {Object.entries(data.rt_coverage.fields).map(([name, field]) => {
              const { tone, key } = rtTone(field);
              return (
                <KeyValue key={name} label={name}>
                  <StatusChip tone={tone}>{t(`admin.agency_diag.${key}`)}</StatusChip>{" "}
                  {field.coverage_pct != null && (
                    <span style={{ color: "var(--text-tertiary)", fontSize: "var(--text-xs)" }}>
                      {t("admin.agency_diag.rt_detail", {
                        pct: field.coverage_pct,
                        samples: field.sample_size ?? 0,
                      })}
                    </span>
                  )}
                </KeyValue>
              );
            })}
          </KeyValueGrid>

          <SectionTitle>{t("admin.agency_diag.static_section")}</SectionTitle>
          {data.static_versions.length === 0 ? (
            <div style={{ fontSize: 12.5, color: "var(--text-tertiary)" }}>
              {t("admin.agency_diag.static_empty")}
            </div>
          ) : (
            <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 6, fontSize: 12.5 }}>
              {data.static_versions.map((v) => (
                <li key={v.version} style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                  <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>
                    {v.version}
                    <span style={{ color: "var(--text-tertiary)" }}>
                      {" · "}
                      {t("admin.agency_diag.static_detail", {
                        routes: v.routes ?? EM_DASH,
                        trips: v.trips ?? EM_DASH,
                      })}
                      {v.calendar_until && ` · ${t("admin.agency_diag.static_calendar", { date: v.calendar_until })}`}
                    </span>
                  </span>
                  {v.is_current && <StatusChip tone="good">{t("admin.agency_diag.static_current")}</StatusChip>}
                </li>
              ))}
            </ul>
          )}

          <SectionTitle>{t("admin.agency_diag.policy_section")}</SectionTitle>
          {editing === "standards" ? (
            <StandardsEditor agencyId={agency.agency_id} rows={data.standards} onDone={() => setEditing(null)} />
          ) : editing === "weights" ? (
            <WeightsEditor agencyId={agency.agency_id} rows={data.weights} onDone={() => setEditing(null)} />
          ) : (
            <KeyValueGrid>
              <KeyValue label={t("admin.agency_diag.standards")}>
                <span>{t("admin.agency_diag.standards_summary", { count: data.standards_count })}</span>{" "}
                <AdminButton
                  variant="secondary"
                  aria-label={t("admin.agency_diag.edit_standards")}
                  onClick={() => setEditing("standards")}
                >
                  {t("admin.agency_diag.edit")}
                </AdminButton>
              </KeyValue>
              <KeyValue label={t("admin.agency_diag.weights")}>
                <span>
                  {t("admin.agency_diag.weights_summary", {
                    withWeights: data.weights_coverage.routes_with_weights,
                    total: data.weights_coverage.routes_total,
                  })}
                </span>{" "}
                <AdminButton
                  variant="secondary"
                  aria-label={t("admin.agency_diag.edit_weights")}
                  onClick={() => setEditing("weights")}
                >
                  {t("admin.agency_diag.edit")}
                </AdminButton>
              </KeyValue>
              <KeyValue label={t("admin.agency_diag.station")}>
                {data.weather_station
                  ? t("admin.agency_diag.station_value", {
                      name: data.weather_station.station_name,
                      id: data.weather_station.station_id,
                    })
                  : t("admin.agency_diag.station_none")}
              </KeyValue>
            </KeyValueGrid>
          )}
        </>
      )}

      <SectionTitle>{t("admin.agency_diag.actions_section")}</SectionTitle>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <AdminButton
          variant="secondary"
          disabled={probe.isPending}
          onClick={() => probe.mutate(agency.agency_id)}
        >
          {t("admin.agency_diag.action_probe")}
        </AdminButton>
        <AdminButton
          variant="secondary"
          disabled={!staticReloadAvailable}
          title={staticReloadAvailable ? undefined : t("admin.agency_diag.action_reload_static_hint")}
        >
          {t("admin.agency_diag.action_reload_static")}
        </AdminButton>
        <AdminButton
          variant="secondary"
          disabled={reanalyze.isPending}
          onClick={() => reanalyze.mutate(agency.agency_id)}
        >
          {t("admin.agency_diag.action_reanalyze")}
        </AdminButton>
      </div>
      {!!probe.error && (
        <div style={{ color: "var(--color-warning)", fontSize: 12.5 }}>{formatApiError(probe.error)}</div>
      )}
      {probe.data && (
        <div style={{ color: "var(--text-tertiary)", fontSize: 12.5 }}>
          {t("admin.agency_diag.probe_done", { samples: probe.data.sample_size ?? 0 })}
        </div>
      )}
      {!!reanalyze.error && (
        <div style={{ color: "var(--color-warning)", fontSize: 12.5 }}>{formatApiError(reanalyze.error)}</div>
      )}
      {reanalyze.data && (
        <div style={{ color: "var(--text-tertiary)", fontSize: 12.5 }}>
          {t("admin.agency_diag.reanalyze_started")}
        </div>
      )}

      <SectionTitle>{t("admin.agency_diag.danger_section")}</SectionTitle>
      {agency.deleted_at ? (
        <div style={{ display: "grid", gap: 8, fontSize: 12.5 }}>
          <span style={{ color: "var(--text-secondary)" }}>{t("admin.agency_diag.disabled_note")}</span>
          <div>
            <AdminButton variant="secondary" onClick={() => onRestore(agency.agency_id)}>
              {t("admin.agency_diag.restore")}
            </AdminButton>
          </div>
        </div>
      ) : (
        <>
          <TypedConfirm
            expected={agency.agency_name}
            hint={t("admin.agency_diag.danger_hint")}
            inputLabel={t("admin.agency_diag.confirm_label")}
            confirmLabel={t("admin.agency_diag.disable")}
            pending={disablePending}
            onConfirm={() => onDisable(agency.agency_id)}
          />
          <div style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)" }}>
            {t("admin.agency_diag.purge_unavailable")}
          </div>
        </>
      )}
    </>
  );
}
