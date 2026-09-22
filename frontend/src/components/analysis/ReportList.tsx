import { useTranslation } from "react-i18next";
import { groupReports, reportDescriptionKey, reportLabel } from "./reportGroups";
import "./ReportList.css";

/** The analysis tab's left-hand report index. Thirteen identically-shaped
 *  buttons said nothing about what any of them was for; grouping them by
 *  intent and giving each a one-line description does.
 *
 *  No sparkline: `useReports` returns `{ report_type, rendered_at }` per
 *  entry and carries no series, so a per-entry trend line would have to be
 *  invented or separately fetched. */
export function ReportList({
  types,
  active,
  onSelect,
}: {
  types: readonly string[];
  active: string | null;
  onSelect: (reportType: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="report-list">
      {groupReports(types).map((group) => {
        const title = t(`reports.group.${group.key}`);
        return (
          <div key={group.key} role="group" aria-label={title}>
            <h4 className="report-list__group-title">{title}</h4>
            {group.types.map((type) => (
              <button
                key={type}
                type="button"
                className="report-list__entry"
                aria-pressed={type === active}
                onClick={() => onSelect(type)}
              >
                <span className="report-list__name">{reportLabel(t, type)}</span>
                <span className="report-list__desc">
                  {t(reportDescriptionKey(type), { defaultValue: "" })}
                </span>
              </button>
            ))}
          </div>
        );
      })}
    </div>
  );
}
