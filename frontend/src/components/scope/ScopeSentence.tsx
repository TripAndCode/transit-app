import { useId, useRef, useState } from "react";
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
  token: ScopeToken & { key: Exclude<TokenKey, "agency"> };
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
  return (
    <span className="scope-token-wrap">
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
 *  control; changes apply at once. A condition the screen's data did not use
 *  (`applied[field] === false`) renders struck through; an absent `applied`
 *  means every condition was used. */
export function ScopeSentence({ applied }: { applied?: Applied }) {
  const { t } = useTranslation();
  const id = useAgencyId();
  const [scope, update] = useScope();
  const { data: agencies } = useAgencies();
  const names = useRouteNames(id);
  const [openKey, setOpenKey] = useState<TokenKey | null>(null);
  const [pinned, setPinned] = useState(readScopePinned);
  const unappliedId = useId();
  const agencyName = agencies?.find((a) => a.agency_id === id)?.agency_name ?? "";
  const tokens = scopeTokens(scope, { t, agencyName, routeLabel: names.format });
  const byKey = new Map(tokens.map((tok) => [tok.key, tok]));
  const separator = t("common.list_separator");

  function renderToken(tok: ScopeToken) {
    if (tok.key === "agency") return <span key="agency" className="scope-agency">{tok.label}</span>;
    const key = tok.key;
    return (
      <Token
        key={key}
        token={{ ...tok, key }}
        off={tok.field != null && applied?.[tok.field] === false}
        describedBy={unappliedId}
        open={openKey === key}
        onToggle={() => setOpenKey(openKey === key ? null : key)}
        onClose={() => setOpenKey(null)}
        scope={scope}
        update={update}
      />
    );
  }

  function togglePinned() {
    const next = !pinned;
    setPinned(next);
    writeScopePinned(next);
  }

  const inline = tokens.filter((tok) => tok.key !== "agency" && tok.key !== "service");

  return (
    <section aria-label={t("scope.label")} className="scope">
      <div className="scope-row">
        <p className="scope-sentence">
          {sentenceParts(t("scope.sentence")).map((part, i) => {
            if ("text" in part) return <span key={`t${i}`}>{part.text}</span>;
            if (part.slot === "extras") {
              return EXTRA_KEYS.flatMap((key) => {
                const tok = byKey.get(key);
                return tok ? [<span key={`s${key}`}>{separator}</span>, renderToken(tok)] : [];
              });
            }
            const tok = byKey.get(part.slot as TokenKey);
            return tok ? renderToken(tok) : null;
          })}
        </p>
        <span id={unappliedId} className="scope-sr-only">
          {t("scope.unapplied")}
        </span>
        <div className="scope-actions">
          {id != null && (
            <PresetMenu agencyId={id} currentRangeCtx={scope} onSelect={(rc) => update(presetScopePatch(rc))} />
          )}
          {differsFromDefault(scope) && (
            <button type="button" className="scope-pill" onClick={() => update(RESET)}>
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
            const Control = CONTROLS[tok.key as Exclude<TokenKey, "agency">];
            return (
              <div key={tok.key} className="scope-strip__cell">
                <div className="scope-strip__label">{t(`scope.popover.${tok.key}`)}</div>
                <Control scope={scope} update={update} />
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
