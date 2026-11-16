import { use, useId, useRef, useState, type FocusEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { useAgencies, useScopeSummary } from "../../api/hooks";
import {
  DataEndContext,
  SCOPE_EXTRAS_NONE,
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
import { readScopePinned, writeScopePinned } from "./scopePin";
import {
  isScopeConditionOff,
  periodDays,
  routeTokenPlates,
  scopeTokens,
  sentenceParts,
  tokenChanged,
  type ScopeApplied,
  type ScopeToken,
  type TokenKey,
} from "./scopePhrases";
import { useScopeActionsSlot } from "./scopeSlot";
import "./scope.css";

type ConditionKey = Exclude<TokenKey, "agency">;

const RESET: ScopePatch = {
  ...SCOPE_EXTRAS_NONE,
  from: null,
  to: null,
  dow: "all",
  time_band: "all",
  service: "all",
  routes: null,
};

/** The conditions the template places by name; every other token is an
 *  optional one and goes into `[extras]`, in the order scopeTokens gives. */
const PLACED_KEYS: ReadonlySet<TokenKey> = new Set(["agency", "routes", "period", "days", "time", "tolerance"]);

/** The controls that draw the summary's figures; the others never fetch it. */
const DATA_KEYS: ReadonlySet<TokenKey> = new Set(["routes", "period", "days", "service", "tolerance"]);

/** Punctuation that must not start a line: it stays with the token before it. */
const TRAILING_PUNCT = /^[、。・，,]+/; // i18n-ignore: punctuation set, not copy

/** Whether a patch removes the condition whose popover is open, taking its
 *  token (and the focus inside the popover) with it. */
function removesOpen(open: ConditionKey | null, patch: ScopePatch): boolean {
  if (open === "stop") return "stop" in patch && patch.stop == null;
  if (open === "dir") return "dir" in patch && patch.dir == null;
  if (open === "early") return "early" in patch && patch.early == null;
  if (open === "service") return "service" in patch && (patch.service == null || patch.service === "all");
  return false;
}

function Token({
  token,
  badge,
  hint,
  on,
  off,
  describedBy,
  open,
  onToggle,
  onClose,
  disabled,
  scope,
  update,
  summary,
}: {
  token: ScopeToken & { key: ConditionKey };
  /** A purely decorative lead-in before the token's own label text (the
   *  routes token's plate stack); never changes what the label says. */
  badge?: ReactNode;
  hint: string | null;
  on: boolean;
  off: boolean;
  describedBy: string;
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
  // Escape already returns focus to the token through the popover's own
  // dismiss handler, and an outside click leaves it where the click landed.
  // Cancel and Apply unmount the very button that held focus, so they
  // refocus the token explicitly, as Escape does.
  function closeAndFocus() {
    onClose();
    ref.current?.focus();
  }
  return (
    <span className="scope-token-wrap" onBlur={onBlur}>
      <button
        ref={ref}
        type="button"
        className={off ? "scope-token scope-token--off" : on ? "scope-token scope-token--on" : "scope-token"}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={off ? t("scope.unapplied") : undefined}
        aria-describedby={off ? describedBy : undefined}
        disabled={disabled}
        onClick={onToggle}
      >
        {badge}
        <span className="scope-token__label">{token.label}</span>
        {token.idTag && <span className="scope-id"> {token.idTag}</span>}
        {hint != null && (
          <>
            {" "}
            <span className="scope-token__hint">{hint}</span>
          </>
        )}
      </button>
      {open && (
        <ScopePopover label={t(`scope.popover.${token.key}`)} onClose={onClose} returnFocusTo={ref}>
          <Control scope={scope} update={update} summary={summary} close={closeAndFocus} />
        </ScopePopover>
      )}
    </span>
  );
}

/** A sentence whose words differ from the default one's, for a screen the
 *  default would misdescribe: Live counts the trips running now, over no
 *  period, and the agencies board compares every agency, not one agency's
 *  routes. A condition such a sentence leaves out still shows in the
 *  not-used note whenever it is set and `applied` says it went unused. */
export type SentenceTemplate = "live" | "board";

/** The scope as one sentence. Each condition is a chip that opens its own
 *  control. A single-choice control applies each change at once; the routes
 *  control stages its picks and commits them only on Apply. A chip changed
 *  from the default is marked. A set condition the screen's data did not use
 *  (`applied[field] === false`) renders struck through instead; an absent
 *  `applied` means every condition was used. The saved views and the pin go
 *  on the page heading's row when the page has a ScopePageHeader, and beside
 *  the sentence otherwise.
 *
 *  The scope is the URL's unless `source` supplies another, as a notebook
 *  thread does with the conditions it keeps for itself. `actions={false}`
 *  leaves out the saved views and the pin, which hold and lay out the URL's
 *  scope. */
export function ScopeSentence({
  applied,
  source,
  template,
  actions: showActions = true,
  disabled = false,
}: {
  applied?: ScopeApplied;
  source?: { scope: Scope; update: (patch: ScopePatch) => void };
  template?: SentenceTemplate;
  actions?: boolean;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const id = useAgencyId();
  const urlScope = useScope();
  const [scope, update] = source ? [source.scope, source.update] : urlScope;
  const pageRoute = use(ScopeRouteContext);
  const { data: agencies } = useAgencies();
  const dataEnd = use(DataEndContext);
  const slot = useScopeActionsSlot();
  const names = useRouteNames(id);
  const place = usePlaceLabels(id, scope.stop != null);
  const [openKey, setOpenKey] = useState<ConditionKey | null>(null);
  const [pinnedPref, setPinned] = useState(readScopePinned);
  const pinned = showActions && pinnedPref;
  const sectionRef = useRef<HTMLElement>(null);
  const unappliedId = useId();
  const agencyName = agencies?.find((a) => a.agency_id === id)?.agency_name ?? "";
  const phrases = {
    t,
    agencyName,
    routeLabel: names.format,
    routeGroup: (code: string) => names.data.get(code),
    routePlate: (code: string) => names.parts.get(code)?.plate ?? null,
    ...place,
  };
  const tokens = scopeTokens(scope, phrases);
  // The data's last day, which ends the default period, arrives with the
  // agencies; until then the period is not judged against a guess.
  const resetPeriod = agencies ? defaultPeriod(dataEnd) : null;
  // A route's own page is about its route: that route is where the page
  // starts, not a filter set on it.
  const onPageRoute = pageRoute != null && scope.routes.length === 1 && scope.routes[0] === pageRoute;
  const isChanged = (key: TokenKey) => (key === "routes" && onPageRoute ? false : tokenChanged(key, scope, resetPeriod));
  const byKey = new Map(tokens.map((tok) => [tok.key, tok]));
  // A condition that was cleared from elsewhere has no popover to show.
  const open = openKey != null && byKey.has(openKey) ? openKey : null;
  // The summary's figures are one agency's over the scope's period: neither
  // the board, which compares every agency, nor Live, which has no period,
  // counts by them.
  const wantsSummary = template == null && (pinned || (open != null && DATA_KEYS.has(open)));
  const { data: summary } = useScopeSummary(id, scope, wantsSummary);
  const separator = t("common.list_separator");

  const isOff = (tok: ScopeToken & { key: ConditionKey }) => isScopeConditionOff(tok, scope, applied);

  function apply(patch: ScopePatch) {
    update(patch);
    if (removesOpen(open, patch)) {
      setOpenKey(null);
      sectionRef.current?.focus();
    }
  }

  function reset() {
    update(RESET);
    setOpenKey(null);
    sectionRef.current?.focus();
  }

  function renderToken(tok: ScopeToken, trailing: string): ReactNode {
    if (tok.key === "agency") {
      return (
        <span key="agency" className="scope-nowrap">
          <span className="scope-agency">{tok.label}</span>
          {trailing}
        </span>
      );
    }
    const key = tok.key;
    const token = { ...tok, key };
    let badge: ReactNode;
    if (key === "routes") {
      const { plates, more } = routeTokenPlates(scope.routes, phrases);
      if (plates.length > 0) {
        badge = (
          <span className="scope-token__plates" aria-hidden="true">
            {plates.map((text, i) => (
              <span key={i} className="route-label__plate scope-token__plate">
                {text}
              </span>
            ))}
            {more > 0 && <span className="route-label__plate scope-token__plate scope-token__plate--more">+{more}</span>}
          </span>
        );
      }
    }
    return (
      <span key={key} className="scope-nowrap">
        <Token
          token={token}
          badge={badge}
          hint={key === "period" ? t("scope.period_days", { count: periodDays(scope) }) : null}
          on={isChanged(key)}
          off={isOff(token)}
          describedBy={unappliedId}
          open={open === key}
          onToggle={() => setOpenKey(open === key ? null : key)}
          onClose={() => setOpenKey(null)}
          disabled={disabled}
          scope={scope}
          update={apply}
          summary={summary}
        />
        {trailing}
      </span>
    );
  }

  // The template's runs, with each optional token behind its separator,
  // flattened so a token can take the punctuation that follows it.
  type Run = { text: string } | { token: ScopeToken };
  const runs: Run[] = [];
  // A screen that never applies the on-time tolerance gets no clause for it
  // unless the scope sets one, which then shows greyed like any unused one.
  const omitTolerance = applied?.late === false && scope.late == null;
  const templateKey = template ? `scope.sentence_${template}` : omitTolerance ? "scope.sentence_no_tolerance" : "scope.sentence";
  for (const part of sentenceParts(t(templateKey))) {
    if ("text" in part) runs.push({ text: part.text });
    else if (part.slot === "extras") {
      for (const tok of tokens) if (!PLACED_KEYS.has(tok.key)) runs.push({ text: separator }, { token: tok });
    } else {
      const tok = byKey.get(part.slot as TokenKey);
      if (tok) runs.push({ token: tok });
    }
  }
  const unused = tokens.filter(
    (tok): tok is ScopeToken & { key: ConditionKey } => tok.key !== "agency" && isOff({ ...tok, key: tok.key }),
  );
  // Reset is offered for what the reader can see: a token in the sentence,
  // or a condition its note names. A template that leaves the period out
  // never offers to reset a period it does not show.
  const shown = new Set<TokenKey>(unused.map((tok) => tok.key));
  for (const run of runs) if ("token" in run) shown.add(run.token.key);
  const changed = tokens.some((tok) => shown.has(tok.key) && isChanged(tok.key));
  const nodes: ReactNode[] = [];
  for (let i = 0; i < runs.length; i++) {
    const run = runs[i];
    if ("text" in run) {
      nodes.push(<span key={`t${i}`}>{run.text}</span>);
      continue;
    }
    const next = runs[i + 1];
    const punct = next && "text" in next ? (TRAILING_PUNCT.exec(next.text)?.[0] ?? "") : "";
    if (punct && next && "text" in next) runs[i + 1] = { text: next.text.slice(punct.length) };
    nodes.push(renderToken(run.token, punct));
  }
  if (changed) {
    nodes.push(
      <button key="reset" type="button" className="scope-reset" aria-label={t("scope.reset")} disabled={disabled} onClick={reset}>
        {t("scope.reset_short")}
      </button>,
    );
  }

  function togglePinned() {
    const next = !pinnedPref;
    setPinned(next);
    writeScopePinned(next);
  }

  const inline = tokens.filter(
    (tok): tok is ScopeToken & { key: ConditionKey } => tok.key !== "agency" && tok.key !== "service",
  );

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
        {/* A div, not a <p>: each token's popover holds block content. */}
        <div className="scope-sentence">{nodes}</div>
        <span id={unappliedId} className="scope-sr-only">
          {t("scope.unapplied")}
        </span>
        {showActions && (slot ? createPortal(actions, slot) : <div className="scope-actions">{actions}</div>)}
      </div>
      {/* The struck-through tokens' reason, in words: a title tooltip alone
          never reaches touch screens. */}
      {unused.length > 0 && (
        <p className="scope-note">
          {t("scope.unapplied_note", { list: unused.map((tok) => t(`scope.popover.${tok.key}`)).join(separator) })}
        </p>
      )}
      {pinned && (
        <div className="scope-strip">
          {inline.map((tok) => {
            const Control = CONTROLS[tok.key];
            const off = isOff(tok);
            return (
              <div key={tok.key} className="scope-strip__cell">
                <div
                  className={off ? "scope-strip__label scope-strip__label--off" : "scope-strip__label"}
                  title={off ? t("scope.unapplied") : undefined}
                >
                  {t(`scope.popover.${tok.key}`)}
                </div>
                <Control scope={scope} update={apply} summary={summary} />
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
