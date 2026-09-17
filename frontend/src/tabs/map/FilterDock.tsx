import { useState } from "react";
import { useTranslation } from "react-i18next";
import { PatternFilters } from "../../components/analysis/AnalysisFilters";

/** Deferred-commit route/pattern filters, mounted as a floating control on
 * the operations map.
 *
 * It sits over the map rather than in a card above it because the map is the
 * screen's subject and the default selection is "every route" — so the
 * control's most common state is untouched, and a full-width card spends the
 * page's most prominent band on that. Floating also puts it in the same
 * language as the map's other controls (legend, style picker, fit-all)
 * instead of introducing a second one.
 *
 * The commit is deferred because the operations map re-derives every counter
 * on each committed change, and re-fits when the change narrows to a single
 * route: applying a half-built selection (a line chosen but its pattern not
 * yet narrowed) churns the whole screen through a state the operator never
 * asked to see. Draft state here keeps the selection local until they say
 * it's ready, and Apply exists only while there is something to apply, so
 * the resting state carries no dead button.
 *
 * `PatternFilters` stays a controlled component shared with the analysis
 * screen, which commits immediately and should keep doing so — the deferral
 * belongs to this caller, not to the control itself.
 */
export function FilterDock({ agencyId, applied, onApply }: {
  agencyId: number | null;
  applied: string[];
  onApply: (codes: string[]) => void;
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
    // A real <form>, so Enter from a focused <select> commits the way a
    // keyboard user expects without a hand-rolled key handler.
    <form
      className={`ops-dock${dirty ? " ops-dock--dirty" : ""}`}
      onSubmit={(e) => { e.preventDefault(); apply(); }}
    >
      <PatternFilters agencyId={agencyId} codes={codes} onChange={setDraft} />
      {dirty && (
        <>
          <button type="submit" className="ops-dock__apply">{t("apply")}</button>
          {/* The button appearing is the visual signal. This says the same
              thing for a screen reader, which can't see the dock's edge
              change colour — hidden rather than dropped so the state is
              announced even though it isn't drawn as prose. */}
          <p className="ops-dock__pending" role="status">{t("pendingChanges")}</p>
        </>
      )}
    </form>
  );
}

function sameCodes(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const seen = new Set(b);
  return a.every((code) => seen.has(code));
}
