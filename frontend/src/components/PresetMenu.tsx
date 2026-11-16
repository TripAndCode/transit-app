import { useId, useRef, useState, type FocusEvent } from "react";
import { flushSync } from "react-dom";
import { useTranslation } from "react-i18next";
import { usePlaceLabels } from "../api/usePlaceLabels";
import { scopeAfterPreset, scopeToQueryString, type Scope } from "../api/scope";
import { NAME_MAX, savedViews, useSavedViews, type SavedView } from "../api/savedViews";
import { ScopePopover } from "./scope/ScopePopover";
import { scopeTitle, suggestedViewName, type PhraseCtx } from "./scope/scopePhrases";

/** Saved views: one pill opening the list, each view with its conditions in
 *  words, a mark on the one on screen and a delete button, plus a form that
 *  names the current scope in place. Views are kept in this browser, so
 *  every visitor gets the same menu whether signed in or not. */
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
  const [menuOpen, setMenuOpen] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState("");
  const [errorReason, setErrorReason] = useState<"name_taken" | "full" | "save_failed" | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const headRef = useRef<HTMLDivElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const baseId = useId();
  const nameId = `${baseId}-name`;

  const presets = useSavedViews(agencyId);
  const { stopName } = usePlaceLabels(agencyId, presets.some((p) => p.range_ctx.stop != null));
  const viewPhrases = { ...phrases, stopName: (id: string) => phrases.stopName?.(id) ?? stopName(id) };

  // A save or delete takes away the control that had focus; the heading
  // keeps it inside the menu. Either one succeeding clears a stale error
  // from the other, which no longer describes the list on screen.
  function settle() {
    setErrorReason(null);
    headRef.current?.focus();
  }

  const currentQuery = scopeToQueryString(currentRangeCtx);
  const showForm = formOpen || presets.length === 0;

  function suggestName() {
    setName(suggestedViewName(currentRangeCtx, { ...viewPhrases, showIds: false }, defaultPeriod).slice(0, NAME_MAX));
  }

  function toggleMenu() {
    if (!menuOpen) {
      suggestName();
      setFormOpen(false);
      setErrorReason(null);
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

  function save() {
    if (!name.trim()) return;
    const result = savedViews.create(agencyId, name, currentRangeCtx);
    if (result.ok) {
      setFormOpen(false);
      settle();
    } else {
      setErrorReason(result.reason);
    }
  }

  function remove(view: SavedView) {
    savedViews.remove(view.id);
    settle();
  }

  return (
    <span className="scope-token-wrap" onBlur={onBlur}>
      <button
        ref={triggerRef}
        type="button"
        className="btn btn--secondary btn--sm"
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
            <span className="preset-head__count">{t("presets.count", { count: presets.length })}</span>
          </div>
          {presets.length > 0 ? (
            <ul className="preset-list">
              {presets.map((p) => {
                const view = scopeAfterPreset(currentRangeCtx, p.range_ctx);
                const active = scopeToQueryString(view) === currentQuery;
                return (
                  <li key={p.id} className={active ? "preset-row preset-row--active" : "preset-row"}>
                    <button
                      type="button"
                      className="preset-row__pick"
                      aria-current={active ? "true" : undefined}
                      aria-labelledby={`${baseId}-n${p.id}`}
                      aria-describedby={`${baseId}-d${p.id}`}
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
                        <span id={`${baseId}-n${p.id}`} className="preset-row__name">
                          {p.name}
                        </span>
                        <span id={`${baseId}-d${p.id}`} className="preset-row__sum">
                          {scopeTitle(view, viewPhrases)}
                        </span>
                      </span>
                    </button>
                    <button
                      type="button"
                      className="preset-row__delete"
                      aria-label={t("presets.delete", { name: p.name })}
                      onClick={() => remove(p)}
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
          {errorReason && (
            <p role="alert" className="preset-error">
              {errorReason === "name_taken"
                ? t("presets.name_taken")
                : errorReason === "full"
                  ? t("presets.full")
                  : t("presets.save_failed")}
            </p>
          )}
          {showForm ? (
            <form
              className="preset-form"
              onSubmit={(e) => {
                e.preventDefault();
                save();
              }}
            >
              <div className="preset-current">
                {t("presets.current")}
                <b>{scopeTitle(currentRangeCtx, viewPhrases)}</b>
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
                <button type="submit" className="preset-save__button" disabled={!name.trim()}>
                  {t("common.save")}
                </button>
              </div>
            </form>
          ) : (
            <button type="button" className="preset-foot" onClick={openForm}>
              <span aria-hidden="true">+ </span>
              {t("presets.save_current")}
            </button>
          )}
        </ScopePopover>
      )}
    </span>
  );
}
