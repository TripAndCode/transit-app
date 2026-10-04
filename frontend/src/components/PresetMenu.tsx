import { useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useSession } from "../api/auth";
import { apiGet, apiPost, formatApiError } from "../api/client";
import type { Scope } from "../api/scope";
import { Modal } from "./Modal";
import { Skeleton } from "./Skeleton";
import { Tooltip } from "./Tooltip";
import { ScopePopover } from "./scope/ScopePopover";

type Preset = { preset_id: number; agency_id: number; name: string; range_ctx: Scope };

/** Saved views: one pill opening the list and "Save this view…", which names
 *  the current scope in a dialog. Signed-out visitors get a hint instead. */
export function PresetMenu({
  agencyId,
  currentRangeCtx,
  onSelect,
}: {
  agencyId: number;
  currentRangeCtx: Scope;
  onSelect: (rangeCtx: Scope) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: session } = useSession();
  const [menuOpen, setMenuOpen] = useState(false);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const triggerRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const nameId = useId();

  const { data: presets, isError: presetsFailed } = useQuery({
    queryKey: ["presets", agencyId],
    queryFn: ({ signal }) => apiGet<Preset[]>(`/api/me/presets?agency_id=${agencyId}`, { signal }),
    enabled: !!session,
  });

  const create = useMutation({
    mutationFn: (n: string) =>
      apiPost<Preset>("/api/me/presets", { agency_id: agencyId, name: n, range_ctx: currentRangeCtx }),
    onSuccess: () => {
      setNaming(false);
      setName("");
      qc.invalidateQueries({ queryKey: ["presets", agencyId] });
    },
  });

  if (!session) {
    return (
      <Tooltip label={t("presets.login_to_save_tooltip")}>
        <span
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- plain label, not a control; keyboard-focusable only so the tooltip explaining why saving is disabled is reachable
          tabIndex={0}
          style={{ color: "var(--text-tertiary)", fontSize: 12 }}
        >
          {t("presets.label")}
        </span>
      </Tooltip>
    );
  }

  const saveDisabled = !name.trim() || create.isPending;

  function startNaming() {
    setMenuOpen(false);
    setName("");
    create.reset();
    setNaming(true);
  }

  return (
    <span className="scope-token-wrap">
      <button
        ref={triggerRef}
        type="button"
        className="scope-pill"
        aria-haspopup="dialog"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((v) => !v)}
      >
        {t("presets.label")}
        <span aria-hidden="true"> ▾</span>
      </button>
      {menuOpen && (
        <ScopePopover label={t("presets.label")} onClose={() => setMenuOpen(false)} returnFocusTo={triggerRef}>
          {presetsFailed ? (
            <p className="scope-note">{t("presets.load_error")}</p>
          ) : presets == null ? (
            <Skeleton height={28} />
          ) : presets.length > 0 ? (
            <ul className="preset-list">
              {presets.map((p) => (
                <li key={p.preset_id}>
                  <button
                    type="button"
                    className="scope-pill"
                    onClick={() => {
                      onSelect(p.range_ctx);
                      setMenuOpen(false);
                    }}
                  >
                    {p.name}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="scope-note">{t("presets.empty")}</p>
          )}
          <button type="button" className="scope-pill" onClick={startNaming}>
            {t("presets.save_current")}
          </button>
        </ScopePopover>
      )}
      <Modal
        open={naming}
        onClose={() => {
          if (!create.isPending) setNaming(false);
        }}
        labelledBy={titleId}
        style={{
          width: "min(400px, calc(100vw - 32px))",
          padding: 20,
          border: "1px solid var(--border-subtle)",
          borderRadius: "var(--radius)",
          boxShadow: "var(--el-2)",
        }}
      >
        <h2 id={titleId} style={{ margin: "0 0 12px", fontSize: 16 }}>
          {t("presets.save_title")}
        </h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!saveDisabled) create.mutate(name.trim());
          }}
        >
          <label htmlFor={nameId} style={{ display: "block", marginBottom: 4, fontSize: "var(--text-sm)" }}>
            {t("presets.name_label")}
          </label>
          <input id={nameId} value={name} onChange={(e) => setName(e.target.value)} style={{ display: "block", width: "100%", marginBottom: 12 }} />
          {create.error && (
            <p role="alert" style={{ margin: "0 0 12px", fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>
              {formatApiError(create.error)}
            </p>
          )}
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button type="button" onClick={() => setNaming(false)} disabled={create.isPending}>
              {t("common.cancel")}
            </button>
            <button type="submit" disabled={saveDisabled}>
              {t("common.save")}
            </button>
          </div>
        </form>
      </Modal>
    </span>
  );
}
