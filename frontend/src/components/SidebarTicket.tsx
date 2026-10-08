import { useMatch } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useScope } from "../api/scope";
import { AgencyPicker } from "./AgencyPicker";
import { daysLabel, periodLabel } from "./scope/scopePhrases";
import "./sidebarLineMap.css";

/** The agency picker and the period the screen on show covers, as one
 *  ticket at the head of the rail. The period is read-only here: the
 *  screen's own scope sentence edits it. Off an agency's pages there is no
 *  period to show. */
export function SidebarTicket() {
  const { t } = useTranslation();
  const onAgencyPage = useMatch("/agencies/:agencyId/*") != null;
  const [scope] = useScope();
  return (
    <div className="rail-ticket">
      <div className="rail-ticket-row">
        <span className="rail-ticket-key">{t("nav.ticket_agency")}</span>
        <AgencyPicker className="rail-ticket-agency" />
      </div>
      {onAgencyPage && (
        <>
          <span className="rail-ticket-perf" aria-hidden="true" />
          <div className="rail-ticket-row">
            <span className="rail-ticket-key">{t("nav.ticket_period")}</span>
            <span className="rail-ticket-period num">{periodLabel(scope, t)}</span>
            <span className="rail-ticket-days">{daysLabel(scope.dow, t)}</span>
          </div>
        </>
      )}
    </div>
  );
}
