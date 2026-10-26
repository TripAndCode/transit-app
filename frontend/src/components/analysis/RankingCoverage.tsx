import { useTranslation } from "react-i18next";

/** Whether groups observed too few times to trust join the ranking. They stay
 *  out by default: a special-day variant seen a few dozen times would
 *  otherwise top a ranking of routes seen hundreds of times. */
export function SparseToggle({
  checked,
  floor,
  onChange,
}: {
  checked: boolean;
  floor: number;
  onChange: (checked: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <label className="ranking-sparse-toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.currentTarget.checked)} />
      {t("reports.ranking_coverage.include_sparse", { floor })}
    </label>
  );
}

/** Why a route list leaves a route out by default, each reason with its own
 *  toggle: a route code the current timetable no longer runs, or a route
 *  whose feed always reports zero delay and so says nothing about
 *  punctuality. */
const LEFT_OUT_LABELS = {
  retired: "reports.ranking_coverage.include_retired",
  zero_delay: "reports.ranking_coverage.include_zero_delay",
} as const;

type LeftOutReason = keyof typeof LEFT_OUT_LABELS;

export function LeftOutToggle({
  reason,
  checked,
  count,
  onChange,
}: {
  reason: LeftOutReason;
  checked: boolean;
  count: number;
  onChange: (checked: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <label className="ranking-sparse-toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.currentTarget.checked)} />
      {t(LEFT_OUT_LABELS[reason], { count })}
    </label>
  );
}

/** How much of the ranking is on screen, with the rest one click away. */
export function RowsShown({ shown, total, onShowAll }: { shown: number; total: number; onShowAll?: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="ranking-rows-shown">
      <span>{t("reports.ranking_coverage.rows_shown", { shown, total })}</span>
      {onShowAll && (
        <button type="button" className="btn-ghost" onClick={onShowAll}>
          {t("reports.ranking_coverage.show_all")}
        </button>
      )}
    </div>
  );
}
