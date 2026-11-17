import { use, useId, useRef, useState, type FocusEvent } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { useAgencies, useScopeSummary } from "../../api/hooks";
import {
  DataEndContext,
  ScopeRouteContext,
  defaultPeriod,
  presetScopePatch,
  useScope,
  type Scope,
  type ScopePatch,
} from "../../api/scope";
import { useAgencyId } from "../../api/useAgencyId";
import type { ScopeSummary } from "../../api/types";
import { useRouteNames } from "../../api/useRouteNames";
import { usePlaceLabels } from "../../api/usePlaceLabels";
import { PresetMenu } from "../PresetMenu";
import { CONTROLS } from "./controlForToken";
import { ScopePopover } from "./ScopePopover";
import { EARLY_ANY } from "../../api/onTimeDefault";
import { directionLetter } from "../../utils/directionLetter";
import { serviceValueLabel, translationT, type LabelT } from "../../utils/filterValueLabels";
import { readScopePinned, writeScopePinned } from "./scopePin";
import {
  conditionUsed,
  durationLabel,
  periodDays,
  scopeTokens,
  tokenChanged,
  type ConditionKey,
  type ScopeApplied,
  type ScopeToken,
} from "./scopePhrases";
import { useScopeActionsSlot } from "./scopeSlot";
import "./scope.css";

type Condition = ScopeToken & { key: ConditionKey };

/** What Reset gives back for each condition. */
const RESET: Record<ConditionKey, ScopePatch> = {
  period: { from: null, to: null },
  days: { dow: "all" },
  service: { service: "all" },
  time: { time_band: "all", hour: null },
  routes: { routes: null },
  stop: { stop: null },
  dir: { dir: null },
  tolerance: { late: null },
  early: { early: null },
};

/** The bar's order: when, then what. */
const BAR_ORDER: readonly ConditionKey[] = ["period", "days", "service", "time", "routes", "stop", "dir", "tolerance", "early"];

/** The controls that draw the summary's figures; the others never fetch it. */
const DATA_KEYS: ReadonlySet<ConditionKey> = new Set(["routes", "period", "days", "service", "tolerance"]);

/** Whether a patch removes the condition whose popover is open, taking its
 *  item (and the focus inside the popover) with it. */
function removesOpen(open: ConditionKey | null, patch: ScopePatch): boolean {
  if (open === "stop") return "stop" in patch && patch.stop == null;
  if (open === "dir") return "dir" in patch && patch.dir == null;
  if (open === "early") return "early" in patch && patch.early == null;
  if (open === "service") return "service" in patch && (patch.service == null || patch.service === "all");
  return false;
}

/** A condition's value as the bar shows it. Its label already names the
 *  condition, so the value leaves the noun out: "Days: All" rather than
 *  "Days: every day", "Timetable: Weekday" rather than "Weekday timetable". */
function barValue(tok: Condition, scope: Scope, t: LabelT): string {
  switch (tok.key) {
    case "routes":
      return scope.routes.length === 0 ? translationT(t, "scope.bar.all") : tok.label;
    case "days":
      return scope.dow === "all" ? translationT(t, "scope.bar.all") : tok.label;
    case "time":
      return scope.hour == null && scope.time_band === "all" ? translationT(t, "scope.bar.all_day") : tok.label;
    case "service":
      return serviceValueLabel(scope.service, t);
    case "dir":
      return scope.dir == null ? tok.label : directionLetter(scope.dir);
    case "early":
      if (scope.early == null) return tok.label;
      return scope.early === EARLY_ANY
        ? translationT(t, "scope.bar.early_any")
        : translationT(t, "scope.bar.early_up_to", { span: durationLabel(scope.early, t) });
    default:
      return tok.label;
  }
}

function Item({
  token,
  value,
  hint,
  changed,
  open,
  onToggle,
  onClose,
  disabled,
  scope,
  update,
  summary,
}: {
  token: Condition;
  value: string;
  hint: string | null;
  changed: boolean;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  disabled: boolean;
  scope: Scope;
  update: (patch: ScopePatch) => void;
  summary: ScopeSummary | undefined;
}) {
  const { t } = useTranslation();
  const ref = useRef<HTMLButtonElement>(null);
  const labelId = useId();
  const valueId = useId();
  const Control = CONTROLS[token.key];
  // Tab walking off the popover's last control leaves it behind; close it so
  // it does not cover what focus moves on to.
  function onBlur(e: FocusEvent<HTMLSpanElement>) {
    if (!open) return;
    const next = e.relatedTarget;
    if (next instanceof Node && e.currentTarget.contains(next)) return;
    if (next == null) return;
    onClose();
  }
  // Escape already returns focus to the control through the popover's own
  // dismiss handler, and an outside click leaves it where the click landed.
  // Cancel and Apply unmount the very button that held focus, so they
  // refocus the control explicitly, as Escape does.
  function closeAndFocus() {
    onClose();
    ref.current?.focus();
  }
  return (
    <span className="scope-bar__item" onBlur={onBlur}>
      <span id={labelId} className="scope-bar__label">
        {t(`scope.bar.${token.key}`)}
      </span>
      <button
        ref={ref}
        type="button"
        className={changed ? "scope-bar__control scope-bar__control--changed" : "scope-bar__control"}
        aria-labelledby={`${labelId} ${valueId}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        disabled={disabled}
        onClick={onToggle}
      >
        <span id={valueId} className="scope-bar__text">
          <span className="scope-bar__value">{value}</span>
          {token.idTag && <span className="scope-id"> {token.idTag}</span>}
          {hint != null && (
            <>
              {" "}
              <span className="scope-bar__hint">{hint}</span>
            </>
          )}
        </span>
      </button>
      {open && (
        <ScopePopover label={t(`scope.popover.${token.key}`)} onClose={onClose} returnFocusTo={ref}>
          {/* A popover open when the bar is disabled stays on screen, its
              controls disabled with it, rather than closing on the reader
              mid-pick. */}
          <fieldset className="scope-fieldset" disabled={disabled}>
            <Control scope={scope} update={update} summary={summary} close={closeAndFocus} disabled={disabled} />
          </fieldset>
        </ScopePopover>
      )}
    </span>
  );
}

/** The scope as a row of labelled conditions, each opening its own control.
 *  A single-choice control applies each change at once; the routes control
 *  stages its picks and commits them only on Apply. A condition changed from
 *  the default is marked. A condition the screen's data does not use
 *  (`applied`, see `conditionUsed`) is left out of the row. While the screen
 *  reloads, its last answer stands, so the row does not grow every condition
 *  and shrink back on each change; before its first answer every condition
 *  shows. The saved views and the pin go on the page
 *  heading's row when the page has a ScopePageHeader, and beside the row
 *  otherwise.
 *
 *  The scope is the URL's unless `source` supplies another, as a notebook
 *  thread does with the conditions it keeps for itself. `actions={false}`
 *  leaves out the saved views and the pin, which hold and lay out the URL's
 *  scope. `figures={false}` keeps the controls from drawing the scope
 *  summary, one agency's daily figures over the period, on a screen that
 *  counts by neither (Live, the agencies board). */
export function ScopeBar({
  applied: appliedNow,
  source,
  actions: showActions = true,
  figures = true,
  disabled = false,
}: {
  applied?: ScopeApplied;
  source?: { scope: Scope; update: (patch: ScopePatch) => void };
  actions?: boolean;
  figures?: boolean;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const [heldApplied, setHeldApplied] = useState<ScopeApplied>(undefined);
  if (appliedNow != null && JSON.stringify(appliedNow) !== JSON.stringify(heldApplied)) setHeldApplied(appliedNow);
  const applied = appliedNow ?? heldApplied;
  const id = useAgencyId();
  const urlScope = useScope();
  const [scope, update] = source ? [source.scope, source.update] : urlScope;
  const pageRoute = use(ScopeRouteContext);
  const { data: agencies } = useAgencies();
  const dataEnd = use(DataEndContext);
  const slot = useScopeActionsSlot();
  // Route and stop names are for their conditions and the saved views'
  // wording; a bar with neither never asks for the agency's routes or stops.
  const names = useRouteNames(conditionUsed("routes", applied) || showActions ? id : null);
  const place = usePlaceLabels(id, scope.stop != null && (conditionUsed("stop", applied) || showActions));
  const [openKey, setOpenKey] = useState<ConditionKey | null>(null);
  const [pinnedPref, setPinned] = useState(readScopePinned);
  const pinned = showActions && pinnedPref;
  const sectionRef = useRef<HTMLElement>(null);
  const agencyName = agencies?.find((a) => a.agency_id === id)?.agency_name ?? "";
  const phrases = {
    t,
    agencyName,
    routeLabel: names.format,
    routeGroup: (code: string) => names.data.get(code),
    ...place,
  };
  // A screen that bands the day but takes no hour reads the band, whatever
  // hour a link carried: the bar and its controls say what the data was cut
  // by. Picking a different band there clears the hour as well.
  const shownScope = scope.hour != null && applied?.hour === false ? { ...scope, hour: null } : scope;
  const byKey = new Map(
    scopeTokens(shownScope, phrases)
      .filter((tok): tok is Condition => tok.key !== "agency" && conditionUsed(tok.key, applied))
      .map((tok) => [tok.key, tok]),
  );
  const conditions = BAR_ORDER.flatMap((key) => byKey.get(key) ?? []);
  // The data's last day, which ends the default period, arrives with the
  // agencies; until then the period is not judged against a guess.
  const resetPeriod = agencies ? defaultPeriod(dataEnd) : null;
  // A route's own page is about its route: that route is where the page
  // starts, not a filter set on it.
  const onPageRoute = pageRoute != null && scope.routes.length === 1 && scope.routes[0] === pageRoute;
  const isChanged = (key: ConditionKey) => (key === "routes" && onPageRoute ? false : tokenChanged(key, shownScope, resetPeriod));
  // A condition that was cleared, or left the row, has no popover to show.
  const open = openKey != null && byKey.has(openKey) ? openKey : null;
  const wantsSummary = figures && (pinned || (open != null && DATA_KEYS.has(open)));
  const { data: summary } = useScopeSummary(id, scope, wantsSummary);
  const changed = conditions.some((tok) => isChanged(tok.key));

  function apply(patch: ScopePatch) {
    update(patch);
    if (removesOpen(open, patch)) {
      setOpenKey(null);
      sectionRef.current?.focus();
    }
  }

  // Reset clears only what the bar shows: a condition the screen hides is
  // never named here, and it still applies on the screens that use it.
  function reset() {
    update(Object.assign({}, ...conditions.filter((tok) => !(tok.key === "routes" && onPageRoute)).map((tok) => RESET[tok.key])));
    setOpenKey(null);
    sectionRef.current?.focus();
  }

  function togglePinned() {
    const next = !pinnedPref;
    setPinned(next);
    writeScopePinned(next);
  }

  const actions = (
    <>
      {id != null && (
        <PresetMenu
          agencyId={id}
          currentRangeCtx={scope}
          phrases={phrases}
          defaultPeriod={resetPeriod}
          onSelect={(rc) => update(presetScopePatch(rc))}
        />
      )}
      <button type="button" className="btn btn--secondary btn--sm" aria-pressed={pinned} onClick={togglePinned}>
        {t("scope.pin")}
      </button>
    </>
  );

  return (
    <section ref={sectionRef} tabIndex={-1} aria-label={t("scope.label")} className="scope">
      <div className="scope-row">
        {/* A div, not a <p>: each item's popover holds block content. */}
        <div className="scope-bar">
          {conditions.map((tok) => (
            <Item
              key={tok.key}
              token={tok}
              value={barValue(tok, shownScope, t)}
              hint={tok.key === "period" ? t("scope.period_days", { count: periodDays(scope) }) : null}
              changed={isChanged(tok.key)}
              open={open === tok.key}
              onToggle={() => setOpenKey(open === tok.key ? null : tok.key)}
              onClose={() => setOpenKey(null)}
              disabled={disabled}
              scope={shownScope}
              update={apply}
              summary={summary}
            />
          ))}
          {changed && (
            <button type="button" className="scope-reset" aria-label={t("scope.reset")} disabled={disabled} onClick={reset}>
              {t("scope.reset_short")}
            </button>
          )}
        </div>
        {showActions && (slot ? createPortal(actions, slot) : <div className="scope-actions">{actions}</div>)}
      </div>
      {pinned && (
        <div className="scope-strip">
          {conditions
            .filter((tok) => tok.key !== "service")
            .map((tok) => {
              const Control = CONTROLS[tok.key];
              return (
                <div key={tok.key} className="scope-strip__cell">
                  <div className="scope-strip__label">{t(`scope.popover.${tok.key}`)}</div>
                  <Control scope={shownScope} update={apply} summary={summary} />
                </div>
              );
            })}
        </div>
      )}
    </section>
  );
}
