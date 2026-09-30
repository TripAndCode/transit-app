import { lazy } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, useLocation, useSearchParams } from "react-router-dom";
import { scopeToQueryString, useScope } from "../api/scope";
import { useAgencyId } from "../api/useAgencyId";
import { SAVED_REPORT_TYPES, destHref, savedTarget } from "../routes/destinations";
import { loadAnalysisTab, loadReportsHomeTab } from "../routes/lazyTabs";
import { SCREEN_STRIP_STYLE, screenStripLinkStyle } from "../components/screenStrip";

const ReportsHomeTab = lazy(loadReportsHomeTab);
const AnalysisTab = lazy(loadAnalysisTab);

type View = "summary" | "saved" | "reports";

const VIEWS: [View, string, Record<string, string>][] = [
  ["summary", "saved.view_summary", {}],
  ["saved", "saved.view_saved", { doc: "saved" }],
  ["reports", "saved.view_reports", { doc: "council" }],
];

function viewOf(doc: string | null, report: string | null): View {
  if ((SAVED_REPORT_TYPES as readonly string[]).includes(report ?? "")) return "reports";
  if (doc === "council" || doc === "certificate") return "reports";
  return doc === "saved" ? "saved" : "summary";
}

/** Reports: the printable period summary, the saved analyses list, and the
 *  export documents (council summary, delay reference), picked by `doc`.
 *  The document links carry only the shared scope plus `doc`: every
 *  document shares one path, and a leftover `report` would force the
 *  export view. */
export function SavedExportTab() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const { search } = useLocation();
  const [ctx] = useScope();
  const id = useAgencyId();
  if (id == null) return null;
  if (params.has("view")) return <Navigate to={savedTarget(id, search)} replace />;
  const doc = params.get("doc");
  const view = viewOf(doc, params.get("report"));
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <nav aria-label={t("saved.title")} style={SCREEN_STRIP_STYLE}>
        {VIEWS.map(([v, key, extra]) => (
          <Link
            key={v}
            to={destHref(id, "reports", scopeToQueryString(ctx), extra)}
            aria-current={v === view ? "page" : undefined}
            style={screenStripLinkStyle(v === view)}
          >
            {t(key)}
          </Link>
        ))}
      </nav>
      <div style={{ flex: 1, minHeight: 0 }}>
        {view === "reports" ? (
          <AnalysisTab
            reportTypes={SAVED_REPORT_TYPES}
            defaultReport={doc === "certificate" ? "delay_certificate" : "council_summary"}
          />
        ) : (
          <ReportsHomeTab />
        )}
      </div>
    </div>
  );
}
