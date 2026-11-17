import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ErrorBanner } from "../../components/ErrorBanner";
import { DataTable, type DataTableColumn } from "../../components/admin/DataTable";
import { PageHeader } from "../../components/ui/PageHeader";
import { apiErrorDetail } from "../../api/client";
import { useAgencies } from "../../api/hooks";
import {
  useAdminTransferPairs,
  useDeleteTransferPair,
  useSaveTransferPair,
  type AdminTransferPair,
  type TransferPairInput,
} from "../../api/admin";
import { AdminButton, StatusChip } from "./adminControls";

const DAY_TYPES = ["weekday", "saturday", "sunday_holiday"] as const;

/** The form's fields as typed: stop ids one comma-separated string, the walk
 *  in minutes, a direction "" for both. */
type Draft = {
  name: string;
  from_agency_id: string;
  from_route_code: string;
  from_direction: string;
  from_stop_ids: string;
  to_agency_id: string;
  to_route_code: string;
  to_direction: string;
  to_stop_ids: string;
  walk_min: string;
  day_types: TransferPairInput["day_types"];
  active: boolean;
};

const EMPTY: Draft = {
  name: "",
  from_agency_id: "",
  from_route_code: "",
  from_direction: "",
  from_stop_ids: "",
  to_agency_id: "",
  to_route_code: "",
  to_direction: "",
  to_stop_ids: "",
  walk_min: "2",
  day_types: [...DAY_TYPES],
  active: true,
};

function toDraft(p: AdminTransferPair): Draft {
  return {
    name: p.name,
    from_agency_id: String(p.from_agency_id),
    from_route_code: p.from_route_code,
    from_direction: p.from_direction == null ? "" : String(p.from_direction),
    from_stop_ids: p.from_stop_ids.join(", "),
    to_agency_id: String(p.to_agency_id),
    to_route_code: p.to_route_code,
    to_direction: p.to_direction == null ? "" : String(p.to_direction),
    to_stop_ids: p.to_stop_ids.join(", "),
    walk_min: String(p.walk_sec / 60),
    day_types: p.day_types,
    active: p.active,
  };
}

const ids = (text: string) =>
  text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
const direction = (text: string): 0 | 1 | null => (text === "0" ? 0 : text === "1" ? 1 : null);

/** The draft as the API takes it. Number checks are the server's; a field
 *  left blank goes as what it is, so the server names it. */
function toInput(d: Draft): TransferPairInput {
  return {
    name: d.name,
    from_agency_id: Number(d.from_agency_id),
    from_route_code: d.from_route_code.trim(),
    from_direction: direction(d.from_direction),
    from_stop_ids: ids(d.from_stop_ids),
    to_agency_id: Number(d.to_agency_id),
    to_route_code: d.to_route_code.trim(),
    to_direction: direction(d.to_direction),
    to_stop_ids: ids(d.to_stop_ids),
    walk_sec: Math.round(Number(d.walk_min) * 60),
    day_types: d.day_types,
    active: d.active,
  };
}

const FIELD = { display: "grid", gap: 4, fontSize: "var(--text-sm)", color: "var(--text-secondary)" } as const;

/** Admin control room: the hub transfers the Transfers board reads. */
export function AdminTransfersPage() {
  const { t } = useTranslation();
  const pairs = useAdminTransferPairs();
  const agencies = useAgencies();
  const save = useSaveTransferPair();
  const remove = useDeleteTransferPair();
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const names = new Map((agencies.data ?? []).map((a) => [a.agency_id, a.agency_name]));

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft((prev) => ({ ...prev, [key]: value }));
  const reset = () => {
    setEditing(null);
    setDraft(EMPTY);
    save.reset();
  };

  const columns: DataTableColumn<AdminTransferPair>[] = [
    { key: "name", header: t("admin.transfers.col_name"), render: (p) => <span style={{ fontWeight: 500 }}>{p.name}</span> },
    {
      key: "from",
      header: t("admin.transfers.col_from"),
      render: (p) => `${names.get(p.from_agency_id) ?? p.from_agency_id} ${p.from_route_code} (${p.from_stop_ids.join(", ")})`,
    },
    {
      key: "to",
      header: t("admin.transfers.col_to"),
      render: (p) => `${names.get(p.to_agency_id) ?? p.to_agency_id} ${p.to_route_code} (${p.to_stop_ids.join(", ")})`,
    },
    { key: "walk", header: t("admin.transfers.col_walk"), render: (p) => t("admin.transfers.minutes", { value: p.walk_sec / 60 }) },
    {
      key: "state",
      header: t("admin.transfers.col_state"),
      render: (p) => (
        <StatusChip tone={p.active ? "good" : "neutral"}>
          {t(p.active ? "admin.transfers.active" : "admin.transfers.inactive")}
        </StatusChip>
      ),
    },
    {
      key: "actions",
      header: t("admin.transfers.col_actions"),
      render: (p) => (
        <span style={{ display: "inline-flex", gap: 8 }}>
          <AdminButton
            variant="secondary"
            onClick={() => {
              save.reset();
              setEditing(p.id);
              setDraft(toDraft(p));
            }}
          >
            {t("admin.transfers.edit")}
          </AdminButton>
          <AdminButton
            variant="secondary"
            disabled={remove.isPending}
            onClick={() => {
              if (window.confirm(t("admin.transfers.delete_confirm", { name: p.name }))) remove.mutate({ id: p.id });
            }}
          >
            {t("admin.transfers.delete")}
          </AdminButton>
        </span>
      ),
    },
  ];

  const side = (prefix: "from" | "to") => (
    <fieldset style={{ border: "1px solid var(--border-soft)", borderRadius: "var(--radius)", padding: 12, display: "grid", gap: 10 }}>
      <legend>{t(`admin.transfers.${prefix}_legend`)}</legend>
      <label style={FIELD}>
        {t("admin.transfers.agency")}
        <select value={draft[`${prefix}_agency_id`]} onChange={(e) => set(`${prefix}_agency_id`, e.target.value)}>
          <option value="">{t("admin.transfers.choose_agency")}</option>
          {(agencies.data ?? []).map((a) => (
            <option key={a.agency_id} value={a.agency_id}>
              {a.agency_name}
            </option>
          ))}
        </select>
      </label>
      <label style={FIELD}>
        {t("admin.transfers.route_code")}
        <input value={draft[`${prefix}_route_code`]} onChange={(e) => set(`${prefix}_route_code`, e.target.value)} />
      </label>
      <label style={FIELD}>
        {t("admin.transfers.direction")}
        <select value={draft[`${prefix}_direction`]} onChange={(e) => set(`${prefix}_direction`, e.target.value)}>
          <option value="">{t("admin.transfers.direction_both")}</option>
          <option value="0">0</option>
          <option value="1">1</option>
        </select>
      </label>
      <label style={FIELD}>
        {t("admin.transfers.stop_ids")}
        <input value={draft[`${prefix}_stop_ids`]} onChange={(e) => set(`${prefix}_stop_ids`, e.target.value)} />
      </label>
    </fieldset>
  );

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
      <PageHeader title={t("admin.transfers.title")} subtitle={t("admin.transfers.subtitle")} />
      {pairs.error != null && <ErrorBanner error={pairs.error} onRetry={() => void pairs.refetch()} />}
      {remove.error != null && <ErrorBanner error={remove.error} message={t("admin.transfers.delete_error")} />}
      <DataTable
        caption={t("admin.transfers.table_label")}
        rows={pairs.data ?? []}
        columns={columns}
        rowKey={(p) => String(p.id)}
        emptyLabel={t("admin.transfers.empty")}
        settled={pairs.data != null}
      />
      <form
        aria-label={t(editing == null ? "admin.transfers.new_title" : "admin.transfers.edit_title")}
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate({ id: editing, body: toInput(draft) }, { onSuccess: reset });
        }}
        style={{ display: "grid", gap: 12, marginTop: 24 }}
      >
        <h2 style={{ margin: 0, fontSize: "var(--text-lg)" }}>
          {t(editing == null ? "admin.transfers.new_title" : "admin.transfers.edit_title")}
        </h2>
        <label style={FIELD}>
          {t("admin.transfers.name")}
          <input value={draft.name} onChange={(e) => set("name", e.target.value)} />
        </label>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
          {side("from")}
          {side("to")}
        </div>
        <label style={FIELD}>
          {t("admin.transfers.walk")}
          <input type="number" min={0} max={30} step={0.5} value={draft.walk_min} onChange={(e) => set("walk_min", e.target.value)} />
        </label>
        <fieldset style={{ border: 0, padding: 0, display: "flex", gap: 16, flexWrap: "wrap" }}>
          <legend style={FIELD}>{t("admin.transfers.day_types")}</legend>
          {DAY_TYPES.map((d) => (
            <label key={d} style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
              <input
                type="checkbox"
                checked={draft.day_types.includes(d)}
                onChange={(e) =>
                  set("day_types", e.target.checked ? DAY_TYPES.filter((x) => x === d || draft.day_types.includes(x)) : draft.day_types.filter((x) => x !== d))
                }
              />
              {t(`transfers.day_type.${d}`)}
            </label>
          ))}
        </fieldset>
        <label style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" checked={draft.active} onChange={(e) => set("active", e.target.checked)} />
          {t("admin.transfers.active")}
        </label>
        {save.error != null && (
          <p role="alert" style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-warning-text)" }}>
            {apiErrorDetail(save.error) ?? t("admin.transfers.save_error")}
          </p>
        )}
        <div style={{ display: "flex", gap: 8 }}>
          <AdminButton variant="primary" type="submit" disabled={save.isPending}>
            {t(editing == null ? "admin.transfers.create" : "admin.transfers.save")}
          </AdminButton>
          {editing != null && (
            <AdminButton variant="secondary" onClick={reset}>
              {t("common.cancel")}
            </AdminButton>
          )}
        </div>
      </form>
    </div>
  );
}
