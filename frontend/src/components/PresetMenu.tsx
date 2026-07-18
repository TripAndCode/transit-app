import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useSession } from "../api/auth";
import { apiGet, apiPost, formatApiError } from "../api/client";
import type { RangeCtx } from "../api/rangeContext";

type Preset = { preset_id: number; agency_id: number; name: string; range_ctx: RangeCtx };

/** Dropdown + save dialog for filter presets; renders a hint when anonymous. */
export function PresetMenu({
  agencyId,
  currentRangeCtx,
  onSelect,
}: {
  agencyId: number;
  currentRangeCtx: RangeCtx;
  onSelect: (rangeCtx: RangeCtx) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: session } = useSession();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");

  const { data: presets } = useQuery({
    queryKey: ["presets", agencyId],
    queryFn: ({ signal }) => apiGet<Preset[]>(`/api/me/presets?agency_id=${agencyId}`, { signal }),
    enabled: !!session,
  });

  const create = useMutation({
    mutationFn: (n: string) =>
      apiPost<Preset>("/api/me/presets", { agency_id: agencyId, name: n, range_ctx: currentRangeCtx }),
    onSuccess: () => {
      setOpen(false);
      setName("");
      qc.invalidateQueries({ queryKey: ["presets", agencyId] });
    },
  });

  if (!session) {
    return (
      <span title={t("presets.login_to_save_tooltip")} style={{ color: "var(--text-tertiary)", fontSize: 12 }}>
        {t("presets.label")}
      </span>
    );
  }

  const saveDisabled = !name.trim() || create.isPending;

  return (
    <div style={{ position: "relative", display: "inline-block" }}>
      <select
        onChange={(e) => {
          const p = presets?.find((x) => String(x.preset_id) === e.target.value);
          if (p) onSelect(p.range_ctx);
        }}
        defaultValue=""
        style={{ marginRight: 8 }}
      >
        <option value="" disabled>{t("presets.option_placeholder")}</option>
        {presets?.map((p) => <option key={p.preset_id} value={p.preset_id}>{p.name}</option>)}
      </select>
      <button
        onClick={() => setOpen(true)}
        style={{
          background: "var(--bg-surface)",
          color: "var(--text-primary)",
          border: "1px solid var(--border-subtle)",
          borderRadius: 4,
          fontSize: 12,
          padding: "5px 10px",
          cursor: "pointer",
        }}
      >
        {t("presets.save_current")}
      </button>
      {open && (
        <div style={{ position: "absolute", top: "100%", left: 0, padding: 12,
                       background: "var(--surface-1)", border: "1px solid var(--surface-2)",
                       borderRadius: 4, zIndex: 10 }}>
          <input
            // eslint-disable-next-line jsx-a11y/no-autofocus -- name field of a just-opened "save preset" popover; focusing it is the expected UX
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("presets.name_placeholder")}
            style={{ display: "block", marginBottom: 8, padding: 4, width: 200 }}
          />
          <button
            disabled={saveDisabled}
            onClick={() => create.mutate(name.trim())}
            style={{
              background: saveDisabled ? "var(--bg-soft)" : "var(--accent)",
              color: saveDisabled ? "var(--text-tertiary)" : "#fff",
              border: "none",
              borderRadius: 4,
              fontSize: 13,
              fontWeight: 500,
              padding: "6px 18px",
              cursor: saveDisabled ? "not-allowed" : "pointer",
              boxShadow: saveDisabled ? "none" : "0 1px 2px rgba(91,108,173,0.25)",
            }}
          >
            {t("common.save")}
          </button>
          <button
            onClick={() => setOpen(false)}
            style={{
              marginLeft: 8,
              background: "transparent",
              color: "var(--text-secondary)",
              border: "1px solid var(--border-soft)",
              borderRadius: 4,
              fontSize: 13,
              padding: "6px 14px",
              cursor: "pointer",
            }}
          >
            {t("common.cancel")}
          </button>
          {create.error && (
            <div style={{ color: "var(--text-tertiary)", fontSize: 12, marginTop: 4 }}>
              {formatApiError(create.error)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
