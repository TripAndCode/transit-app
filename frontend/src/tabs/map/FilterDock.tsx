import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Clapperboard } from "lucide-react";
import { PatternFilters } from "../../components/analysis/AnalysisFilters";
import { sameCodes } from "../../utils/sameCodes";

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
 *
 * Day playback is offered here rather than as a fourth floating control: it is
 * a way of looking at the same filtered selection, so it belongs beside the
 * filters that define it — and it is the one control on the map that replaces
 * what the map is showing rather than reframing it, which is worth saying by
 * placement as well as by label.
 */
export function FilterDock({ agencyId, applied, onApply, playback }: {
  agencyId: number | null;
  applied: string[];
  onApply: (codes: string[]) => void;
  playback?: { active: boolean; onToggle: () => void };
}) {
  const { t } = useTranslation("design");
  const { t: tc } = useTranslation();
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
      {playback && (
        <button
          type="button"
          className={`ops-dock__playback${playback.active ? " ops-dock__playback--on" : ""}`}
          aria-pressed={playback.active}
          onClick={playback.onToggle}
        >
          <Clapperboard size={14} aria-hidden="true" />
          {tc(playback.active ? "operations.playback.toggle_off" : "operations.playback.toggle_on")}
        </button>
      )}
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
