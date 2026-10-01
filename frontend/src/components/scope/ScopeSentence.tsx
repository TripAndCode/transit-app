import { useId, useRef, useState, type FocusEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useAgencies } from "../../api/hooks";
import { SCOPE_EXTRAS_NONE, presetScopePatch, useScope, type Scope, type ScopePatch } from "../../api/scope";
import { useAgencyId } from "../../api/useAgencyId";
import { useRouteNames } from "../../api/useRouteNames";
import { PresetMenu } from "../PresetMenu";
import { CONTROLS } from "./controlForToken";
import { ScopePopover } from "./ScopePopover";
import { readScopePinned, writeScopePinned } from "./scopePin";
import { scopeTokens, sentenceParts, type ScopeField, type ScopeToken, type TokenKey } from "./scopePhrases";
import "./scope.css";

/** Keyed by URL param name, as the API reports it (api/scope_applied.py). */
type Applied = Partial<Record<ScopeField | (string & {}), boolean>> | null | undefined;
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

const EXTRA_KEYS: readonly TokenKey[] = ["service", "stop", "dir"];

/** Punctuation that must not start a line: it stays with the token before it. */
const TRAILING_PUNCT = /^[、。・，,]+/; // i18n-ignore: punctuation set, not copy

function differsFromDefault(scope: Scope): boolean {
  return (
    scope.dow !== "all" ||
    scope.time_band !== "all" ||
    scope.service !== "all" ||
    scope.routes.length > 0 ||
    scope.hour != null ||
    scope.stop != null ||
    scope.dir != null ||
    scope.late != null ||
    scope.early != null
  );
}

/** Whether a condition narrows anything. A condition at its default filters
 *  nothing, so a screen ignoring it is not worth striking through. */
function isSet(key: ConditionKey, scope: Scope): boolean {
  switch (key) {
    case "routes":
      return scope.routes.length > 0;
    case "days":
      return scope.dow !== "all";
    case "time":
      return scope.hour != null || scope.time_band !== "all";
    case "tolerance":
      return scope.late != null;
    default:
      return true;
  }
}

/** Whether a patch removes the condition whose popover is open, taking its
 *  token (and the focus inside the popover) with it. */
function removesOpen(open: ConditionKey | null, patch: ScopePatch): boolean {
  if (open === "stop") return "stop" in patch && patch.stop == null;
  if (open === "dir") return "dir" in patch && patch.dir == null;
  if (open === "service") return "service" in patch && (patch.service == null || patch.service === "all");
  return false;
}

function Token({
  token,
  off,
  describedBy,
  open,
  onToggle,
  onClose,
  scope,
  update,
}: {
  token: ScopeToken & { key: ConditionKey };
  off: boolean;
  describedBy: string;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  scope: Scope;
  update: (patch: ScopePatch) => void;
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
  return (
    <span className="scope-token-wrap" onBlur={onBlur}>
      <button
        ref={ref}
        type="button"
        className={off ? "scope-token scope-token--off" : "scope-token"}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={off ? t("scope.unapplied") : undefined}
        aria-describedby={off ? describedBy : undefined}
        onClick={onToggle}
      >
        {token.label}
      </button>
      {open && (
        <ScopePopover label={t(`scope.popover.${token.key}`)} onClose={onClose} returnFocusTo={ref}>
          <Control scope={scope} update={update} />
        </ScopePopover>
      )}
    </span>
  );
}

/** The scope as one sentence. Each condition is a button that opens its own
 *  control; changes apply at once. A set condition the screen's data did not
 *  use (`applied[field] === false`) renders struck through; an absent
 *  `applied` means every condition was used. */
export function ScopeSentence({ applied }: { applied?: Applied }) {
  const { t } = useTranslation();
  const id = useAgencyId();
  const [scope, update] = useScope();
  const { data: agencies } = useAgencies();
  const names = useRouteNames(id);
  const [openKey, setOpenKey] = useState<ConditionKey | null>(null);
  const [pinned, setPinned] = useState(readScopePinned);
  const sectionRef = useRef<HTMLElement>(null);
  const unappliedId = useId();
  const agencyName = agencies?.find((a) => a.agency_id === id)?.agency_name ?? "";
  const tokens = scopeTokens(scope, {
    t,
    agencyName,
    routeLabel: names.format,
    routeGroup: (code) => names.data.get(code),
  });
  const byKey = new Map(tokens.map((tok) => [tok.key, tok]));
  // A condition that was cleared from elsewhere has no popover to show.
  const open = openKey != null && byKey.has(openKey) ? openKey : null;
  const separator = t("common.list_separator");

  const isOff = (tok: ScopeToken & { key: ConditionKey }) =>
    tok.field != null && applied?.[tok.field] === false && isSet(tok.key, scope);

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
    return (
      <span key={key} className="scope-nowrap">
        <Token
          token={token}
          off={isOff(token)}
          describedBy={unappliedId}
          open={open === key}
          onToggle={() => setOpenKey(open === key ? null : key)}
          onClose={() => setOpenKey(null)}
          scope={scope}
          update={apply}
        />
        {trailing}
      </span>
    );
  }

  // The template's runs, with each optional token behind its separator,
  // flattened so a token can take the punctuation that follows it.
  type Run = { text: string } | { token: ScopeToken };
  const runs: Run[] = [];
  for (const part of sentenceParts(t("scope.sentence"))) {
    if ("text" in part) runs.push({ text: part.text });
    else if (part.slot === "extras") {
      for (const key of EXTRA_KEYS) {
        const tok = byKey.get(key);
        if (tok) runs.push({ text: separator }, { token: tok });
      }
    } else {
      const tok = byKey.get(part.slot as TokenKey);
      if (tok) runs.push({ token: tok });
    }
  }
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

  function togglePinned() {
    const next = !pinned;
    setPinned(next);
    writeScopePinned(next);
  }

  const inline = tokens.filter(
    (tok): tok is ScopeToken & { key: ConditionKey } => tok.key !== "agency" && tok.key !== "service",
  );

  return (
    <section ref={sectionRef} tabIndex={-1} aria-label={t("scope.label")} className="scope">
      <div className="scope-row">
        {/* A div, not a <p>: each token's popover holds block content. */}
        <div className="scope-sentence">{nodes}</div>
        <span id={unappliedId} className="scope-sr-only">
          {t("scope.unapplied")}
        </span>
        <div className="scope-actions">
          {id != null && (
            <PresetMenu agencyId={id} currentRangeCtx={scope} onSelect={(rc) => update(presetScopePatch(rc))} />
          )}
          {differsFromDefault(scope) && (
            <button type="button" className="scope-pill" onClick={reset}>
              {t("scope.reset")}
            </button>
          )}
          <button type="button" className="scope-pill" aria-pressed={pinned} onClick={togglePinned}>
            {t("scope.pin")}
          </button>
        </div>
      </div>
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
                <Control scope={scope} update={apply} />
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
