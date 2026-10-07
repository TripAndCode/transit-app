import { useTranslation } from "react-i18next";

/** Under a report table with a service column: "Service" names the timetable
 *  a trip ran on, which reads like a second day column until it is said. */
export function ServiceNote() {
  const { t } = useTranslation();
  return <p className="report-footnote">{t("reports.service_note")}</p>;
}
