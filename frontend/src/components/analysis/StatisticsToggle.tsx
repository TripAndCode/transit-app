import { useTranslation } from "react-i18next";
import { useShowStatistics } from "../../api/statisticsPref";
import "./StatisticsToggle.css";

/** The one switch for the tables' statistics-only columns, remembered for
 *  this browser. Exports keep every column whatever it says. */
export function StatisticsToggle() {
  const { t } = useTranslation();
  const [on, setOn] = useShowStatistics();
  return (
    <label className="statistics-toggle">
      <input type="checkbox" checked={on} onChange={(e) => setOn(e.currentTarget.checked)} />
      {t("reports.statistics_toggle")}
    </label>
  );
}
