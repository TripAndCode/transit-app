import { useId, useRef, useState, type FocusEvent } from "react";
import { flushSync } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useSession } from "../api/auth";
import { ApiError, apiDelete, apiGet, apiPost, formatApiError } from "../api/client";
import { scopeAfterPreset, scopeToQueryString, type Scope } from "../api/scope";
import { Skeleton } from "./Skeleton";
import { Tooltip } from "./Tooltip";
import { ScopePopover } from "./scope/ScopePopover";
import { scopeTitle, suggestedViewName, type PhraseCtx } from "./scope/scopePhrases";

/** `range_ctx` is stored JSON: a view saved before a scope field existed
 *  lacks it, so it is read back through scopeAfterPreset, never as a Scope. */
type Preset = { preset_id: number; agency_id: number; name: string; range_ctx: Record<string, unknown> };

/** PresetIn.name's limit in api/routers/me.py. */
const NAME_MAX = 120;

/** Saved views: one pill opening the list, each view with its conditions in
 *  words, a mark on the one on screen and a delete button, plus a form that
 *  names the current scope in place. Signed-out visitors get a hint instead. */
export function PresetMenu({
  agencyId,
  currentRangeCtx,
  phrases,
  defaultPeriod,
  onSelect,
}: {
  agencyId: number;
  currentRangeCtx: Scope;
  /** How the scope sentence says conditions, so a view reads the same. */
  phrases: PhraseCtx;
  /** What a reset gives back, or null while unknown; it decides which
   *  conditions the suggested name mentions. */
  defaultPeriod: Pick<Scope, "from" | "to"> | null;
  onSelect: (rangeCtx: Record<string, unknown>) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: session } = useSession();
  const [menuOpen, setMenuOpen] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState("");
  const triggerRef = useRef<HTMLButtonElement>(null);
  const headRef = useRef<HTMLDivElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const baseId = useId();
  const nameId = `${baseId}-name`;

  const { data: presets, isError: presetsFailed } = useQuery({
    queryKey: ["presets", agencyId],
    queryFn: ({ signal }) => apiGet<Preset[]>(`/api/me/presets?agency_id=${agencyId}`, { signal }),
    enabled: !!session,
  });

  const create = useMutation({
    mutationFn: (n: string) =>
      apiPost<Preset>("/api/me/presets", { agency_id: agencyId, name: n, range_ctx: currentRangeCtx }),
    onSuccess: () => {
      setFormOpen(false);
      settle();
    },
  });

  const remove = useMutation({
    mutationFn: (presetId: number) => apiDelete(`/api/me/presets/${presetId}`),
    onSuccess: () => settle(),
  });

  // A save or delete takes away the control that had focus; the heading
  // keeps it inside the menu. Either one succeeding clears the other's
  // error, which no longer describes the list on screen.
  function settle() {
    create.reset();
    remove.reset();
    void qc.invalidateQueries({ queryKey: ["presets", agencyId] });
    headRef.current?.focus();
  }

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

  const currentQuery = scopeToQueryString(currentRangeCtx);
  const showForm = formOpen || presets?.length === 0;
  const error = create.error ?? remove.error;

  function suggestName() {
    setName(suggestedViewName(currentRangeCtx, phrases, defaultPeriod).slice(0, NAME_MAX));
  }

  function toggleMenu() {
    if (!menuOpen) {
      suggestName();
      setFormOpen(false);
      create.reset();
      remove.reset();
    }
    setMenuOpen(!menuOpen);
  }

  function openForm() {
    suggestName();
    flushSync(() => setFormOpen(true));
    nameRef.current?.focus();
  }

  // Tab walking off the menu's last control leaves it behind; close it so it
  // does not cover what focus moves on to.
  function onBlur(e: FocusEvent<HTMLSpanElement>) {
    if (!menuOpen) return;
    const next = e.relatedTarget;
    if (next == null) return;
    if (next instanceof Node && e.currentTarget.contains(next)) return;
    setMenuOpen(false);
  }

  return (
    <span className="scope-token-wrap" onBlur={onBlur}>
      <button
        ref={triggerRef}
        type="button"
        className="scope-pill"
        aria-haspopup="dialog"
        aria-expanded={menuOpen}
        onClick={toggleMenu}
      >
        {t("presets.label")}
        <span aria-hidden="true"> ▾</span>
      </button>
      {menuOpen && (
        <ScopePopover
          label={t("presets.label")}
          onClose={() => setMenuOpen(false)}
          returnFocusTo={triggerRef}
          className="preset-popover"
        >
          <div ref={headRef} tabIndex={-1} className="preset-head">
            <span className="preset-head__title">{t("presets.label")}</span>
            {presets != null && <span className="preset-head__count">{t("presets.count", { count: presets.length })}</span>}
          </div>
          {presetsFailed ? (
            <p className="preset-note">{t("presets.load_error")}</p>
          ) : presets == null ? (
            <div className="preset-note">
              <Skeleton height={28} />
            </div>
          ) : presets.length > 0 ? (
            <ul className="preset-list">
              {presets.map((p) => {
                const view = scopeAfterPreset(currentRangeCtx, p.range_ctx);
                const active = scopeToQueryString(view) === currentQuery;
                return (
                  <li key={p.preset_id} className={active ? "preset-row preset-row--active" : "preset-row"}>
                    <button
                      type="button"
                      className="preset-row__pick"
                      aria-current={active ? "true" : undefined}
                      aria-labelledby={`${baseId}-n${p.preset_id}`}
                      aria-describedby={`${baseId}-d${p.preset_id}`}
                      onClick={() => {
                        onSelect(p.range_ctx);
                        setMenuOpen(false);
                        triggerRef.current?.focus();
                      }}
                    >
                      <span className="preset-row__check" aria-hidden="true">
                        {active ? "✓" : ""}
                      </span>
                      <span className="preset-row__text">
                        <span id={`${baseId}-n${p.preset_id}`} className="preset-row__name">
                          {p.name}
                        </span>
                        <span id={`${baseId}-d${p.preset_id}`} className="preset-row__sum">
                          {scopeTitle(view, phrases)}
                        </span>
                      </span>
                    </button>
                    <button
                      type="button"
                      className="preset-row__delete"
                      aria-label={t("presets.delete", { name: p.name })}
                      aria-disabled={remove.isPending || undefined}
                      onClick={() => {
                        if (!remove.isPending) remove.mutate(p.preset_id);
                      }}
                    >
                      <span aria-hidden="true">×</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="preset-note">{t("presets.intro")}</p>
          )}
          {error && (
            <p role="alert" className="preset-error">
              {error instanceof ApiError && error.status === 409 ? t("presets.name_taken") : formatApiError(error)}
            </p>
          )}
          {showForm ? (
            <form
              className="preset-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (name.trim() && !create.isPending) create.mutate(name.trim());
              }}
            >
              <div className="preset-current">
                {t("presets.current")}
                <b>{scopeTitle(currentRangeCtx, phrases)}</b>
              </div>
              <div className="preset-save">
                <label htmlFor={nameId} className="scope-sr-only">
                  {t("presets.name_label")}
                </label>
                <input
                  ref={nameRef}
                  id={nameId}
                  className="preset-save__name"
                  value={name}
                  maxLength={NAME_MAX}
                  onChange={(e) => setName(e.target.value)}
                />
                {/* A pending save or delete is aria-disabled, not disabled:
                    disabling the pressed button would drop focus to the
                    page, and a failure would leave it there. */}
                <button
                  type="submit"
                  className="preset-save__button"
                  disabled={!name.trim()}
                  aria-disabled={create.isPending || undefined}
                >
                  {t("common.save")}
                </button>
              </div>
            </form>
          ) : (
            presets != null || presetsFailed ? (
              <button type="button" className="preset-foot" onClick={openForm}>
                <span aria-hidden="true">+ </span>
                {t("presets.save_current")}
              </button>
            ) : null
          )}
        </ScopePopover>
      )}
    </span>
  );
}
