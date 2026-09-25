import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ErrorBanner } from "../../components/ErrorBanner";
import { Modal } from "../../components/Modal";
import { DataTable, type DataTableColumn } from "../../components/admin/DataTable";
import {
  useClearFeatureFlag,
  useFeatureFlags,
  usePatchFeatureFlag,
  type FeatureFlag,
} from "../../api/admin";
import { formatDateTime } from "../../utils/format";
import { AdminButton, StatusChip } from "./adminControls";

/** The flag a toggle click opened a reason dialog for, plus the value it
 * would move to if confirmed -- captured at click time so a slow query
 * refetch between the click and the confirm can't change what gets sent. */
type PendingChange = { flag: FeatureFlag; nextValue: boolean };

function FlagReasonDialog({
  pending,
  onCancel,
  onConfirm,
  isPending,
}: {
  pending: PendingChange;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
  isPending: boolean;
}) {
  const { t } = useTranslation();
  const [reason, setReason] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const label = t(pending.flag.label_key);
  const title = t(pending.nextValue ? "admin.flags.dialog_title_enable" : "admin.flags.dialog_title_disable", {
    label,
  });

  const trimmed = reason.trim();

  return (
    <Modal
      open
      onClose={onCancel}
      ariaLabel={title}
      initialFocusRef={textareaRef}
      style={{ width: "min(420px, 92vw)" }}
    >
        <h3 style={{ margin: "0 0 14px", fontSize: "var(--text-base)", fontWeight: 700 }}>{title}</h3>
        <label style={{ display: "block", fontSize: "var(--text-sm)", color: "var(--text-secondary)", marginBottom: 6 }}>
          {t("admin.flags.reason_label")}
        </label>
        <textarea
          ref={textareaRef}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder={t("admin.flags.reason_placeholder")}
          rows={3}
          style={{
            width: "100%",
            resize: "vertical",
            borderRadius: 6,
            border: "1px solid var(--border-subtle)",
            padding: "8px 10px",
            fontSize: "var(--text-sm)",
            fontFamily: "inherit",
            boxSizing: "border-box",
          }}
        />
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 18 }}>
          <AdminButton variant="secondary" onClick={onCancel} disabled={isPending}>
            {t("admin.flags.cancel")}
          </AdminButton>
          <AdminButton
            variant="primary"
            onClick={() => onConfirm(trimmed)}
            disabled={trimmed === "" || isPending}
          >
            {t("admin.flags.confirm")}
          </AdminButton>
        </div>
    </Modal>
  );
}

function FlagToggle({ flag, onRequestChange }: { flag: FeatureFlag; onRequestChange: (next: boolean) => void }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      role="switch"
      aria-checked={flag.value}
      aria-label={t(flag.label_key)}
      onClick={() => onRequestChange(!flag.value)}
      style={{
        width: 40,
        height: 22,
        borderRadius: 999,
        border: "1px solid var(--border-subtle)",
        background: flag.value ? "var(--accent)" : "var(--surface-2)",
        position: "relative",
        cursor: "pointer",
        padding: 0,
        flexShrink: 0,
      }}
    >
      {/* The knob tracks the state rather than staying one colour: it has to
          read against `--accent` when on and `--surface-2` when off, and no
          single token contrasts with both in both themes. */}
      <span
        aria-hidden="true"
        style={{
          position: "absolute",
          top: 1,
          left: flag.value ? 19 : 1,
          width: 18,
          height: 18,
          borderRadius: "50%",
          background: flag.value ? "var(--bg-surface)" : "var(--text-tertiary)",
          transition: "left var(--dur-1) var(--ease-out)",
        }}
      />
    </button>
  );
}

function formatUpdatedAt(iso: string | null): string {
  if (iso === null) return "";
  return formatDateTime(iso);
}

export function AdminFlagsPage() {
  const { t } = useTranslation();
  const { data, error, refetch } = useFeatureFlags();
  const patch = usePatchFeatureFlag();
  const clear = useClearFeatureFlag();
  const [pending, setPending] = useState<PendingChange | null>(null);

  const columns: DataTableColumn<FeatureFlag>[] = [
    {
      key: "flag",
      header: t("admin.flags.col_flag"),
      render: (f) => <span style={{ fontWeight: 500 }}>{t(f.label_key)}</span>,
    },
    {
      key: "value",
      header: t("admin.flags.col_value"),
      render: (f) => <FlagToggle flag={f} onRequestChange={(nextValue) => setPending({ flag: f, nextValue })} />,
    },
    {
      key: "source",
      header: t("admin.flags.col_source"),
      render: (f) => (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <StatusChip tone={f.source === "override" ? "good" : "neutral"}>
            {t(f.source === "override" ? "admin.flags.source_override" : "admin.flags.source_env")}
          </StatusChip>
          {f.source === "override" && (
            <AdminButton
              variant="secondary"
              disabled={clear.isPending}
              onClick={() => clear.mutate({ key: f.key })}
            >
              {t("admin.flags.clear_override")}
            </AdminButton>
          )}
        </span>
      ),
    },
    {
      key: "updated",
      header: t("admin.flags.col_updated"),
      render: (f) =>
        f.source === "override" ? (
          <span style={{ color: "var(--text-tertiary)", fontSize: "var(--text-sm)" }}>
            {f.updated_by !== null && <div>{t("admin.flags.updated_by", { id: f.updated_by })}</div>}
            <div>{formatUpdatedAt(f.updated_at)}</div>
            <div>{f.reason ?? t("admin.flags.no_reason")}</div>
          </span>
        ) : null,
    },
  ];

  return (
    <div style={{ padding: 24, maxWidth: 900 }}>
      <h1 style={{ fontSize: 22, marginBottom: 20 }}>{t("admin.flags.title")}</h1>

      {clear.error !== null && <ErrorBanner error={clear.error} message={t("admin.flags.clear_error")} />}
      {error != null && <ErrorBanner error={error} onRetry={refetch} />}

      <DataTable
        caption={t("admin.flags.table_label")}
        rows={data ?? []}
        columns={columns}
        rowKey={(f) => f.key}
        emptyLabel={t("admin.flags.empty")}
      />

      {pending && (
        <FlagReasonDialog
          pending={pending}
          isPending={patch.isPending}
          onCancel={() => setPending(null)}
          onConfirm={(reason) => {
            patch.mutate(
              { key: pending.flag.key, value: pending.nextValue, reason },
              { onSuccess: () => setPending(null) }
            );
          }}
        />
      )}
    </div>
  );
}
