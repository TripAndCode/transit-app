import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { PatternFilters } from "../../components/analysis/AnalysisFilters";

/** Deferred-commit wrapper around the route/pattern filters.
 *
 * The operations map re-queries live trips, re-derives every counter, and
 * re-fits the map on each committed filter change, so applying a half-built
 * selection (a route chosen but its pattern not yet narrowed) makes the whole
 * screen churn through a state the operator never asked to see. Draft state
 * here keeps the selection local until they say it's ready.
 *
 * `PatternFilters` stays a controlled component shared with the analysis
 * screen, which commits immediately and should keep doing so — the deferral
 * belongs to this caller, not to the control itself.
 */
export function FilterDock({ agencyId, applied, onApply, trailing }: {
  agencyId: number | null;
  applied: string[];
  onApply: (codes: string[]) => void;
  trailing?: ReactNode;
}) {
  const { t } = useTranslation("design");
  const [draft, setDraft] = useState<string[] | null>(null);
  const codes = draft ?? applied;
  // Compared as sets, not arrays: `PatternFilters` rebuilds a whole group's
  // code list on a line change, so an unchanged selection can come back in a
  // different order and would otherwise read as pending forever.
  const dirty = draft !== null && !sameCodes(draft, applied);

  function apply() {
    if (!dirty) return;
    onApply(codes);
    setDraft(null);
  }

  return (
    <div className={`ops-dock${dirty ? " ops-dock--dirty" : ""}`}>
      {/* A real <form>, so Enter from a focused <select> commits the way a
          keyboard user expects without a hand-rolled key handler. */}
      <form
        className="ops-dock__controls"
        onSubmit={(e) => { e.preventDefault(); apply(); }}
      >
        <PatternFilters agencyId={agencyId} codes={codes} onChange={setDraft} />
        <button type="submit" className="ops-dock__apply" disabled={!dirty}>
          {t("apply")}
        </button>
      </form>
      {dirty && <p className="ops-dock__pending" role="status">{t("pendingChanges")}</p>}
      {trailing}
    </div>
  );
}

function sameCodes(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const seen = new Set(b);
  return a.every((code) => seen.has(code));
}
