import { useEffect, useState } from "react";
import { flushSync } from "react-dom";
import { useTranslation } from "react-i18next";
import { useAgencies } from "../api/hooks";
import { useScope } from "../api/scope";
import { useAgencyId } from "../api/useAgencyId";
import { useRouteNames } from "../api/useRouteNames";
import { formatDateTime } from "../utils/format";
import { scopeTitle } from "./scope/scopePhrases";

/** More routes than this read as a list nobody can scan on paper; the header's
 *  "N routes" already says how many. */
const MAX_NAMED_ROUTES = 6;

const STAMP_OPTIONS: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Tokyo" };

/** The ISO time of the latest print, or null before the first. Read off the
 *  `beforeprint` event, never the clock during render, so the stamp is the
 *  moment the page was printed and a render stays a pure function of its inputs. */
function usePrintedAt(): string | null {
  const [printedAt, setPrintedAt] = useState<string | null>(null);
  useEffect(() => {
    // flushSync: the browser lays the page out right after this event, so the
    // new text has to be in the DOM before the handler returns.
    const stamp = () => flushSync(() => setPrintedAt(new Date().toISOString()));
    window.addEventListener("beforeprint", stamp);
    return () => window.removeEventListener("beforeprint", stamp);
  }, []);
  return printedAt;
}

/** What a printed page cannot show on its own: the URL is lost on paper, and
 *  the agency, window, service pattern and routes that produced the numbers
 *  live in it. Hidden on screen; the print stylesheet shows it. */
export function PrintFrame() {
  const { t } = useTranslation();
  const agencyId = useAgencyId();
  const agencyName = useAgencies().data?.find((a) => a.agency_id === agencyId)?.agency_name ?? "";
  const [scope] = useScope();
  const names = useRouteNames(agencyId);
  const printedAt = usePrintedAt();
  if (agencyId == null) return null;

  const conditions = scopeTitle(scope, { t, agencyName, routeLabel: names.format });
  const named = scope.routes.length > 0 && scope.routes.length <= MAX_NAMED_ROUTES;
  return (
    <div className="print-frame" data-testid="print-frame">
      <header className="print-frame__header" data-testid="print-frame-header">
        <strong>{agencyName}</strong>
        <span>{conditions}</span>
        {named && (
          <span data-testid="print-frame-routes">
            {t("print.routes", { names: scope.routes.map((code) => names.format(code)).join(t("common.list_separator")) })}
          </span>
        )}
      </header>
      <footer className="print-frame__footer" data-testid="print-frame-footer">
        {printedAt && t("print.generated_at", { time: formatDateTime(printedAt, STAMP_OPTIONS) })}
      </footer>
    </div>
  );
}
