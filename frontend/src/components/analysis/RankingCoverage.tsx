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
