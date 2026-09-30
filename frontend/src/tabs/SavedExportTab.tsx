import { lazy } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { scopeToQueryString, useScope } from "../api/scope";
import { useAgencyId } from "../api/useAgencyId";
import { SAVED_REPORT_TYPES, mergeSearch } from "../routes/analysisRoutes";
import { loadAnalysisTab, loadReportsHomeTab } from "../routes/lazyTabs";
import { SCREEN_STRIP_STYLE, screenStripLinkStyle } from "../components/screenStrip";

const ReportsHomeTab = lazy(loadReportsHomeTab);
const AnalysisTab = lazy(loadAnalysisTab);

type View = "summary" | "saved" | "reports";

const VIEWS: [View, string][] = [
  ["summary", "saved.view_summary"],
  ["saved", "saved.view_saved"],
  ["reports", "saved.view_reports"],
];

/** Saved & export: the printable period summary, the saved analyses list,
 *  and the export-style report types (council summary, delay reference).
 *  The view links carry only the shared scope plus `view`: every view shares
 *  one path, and a leftover `report` would force the reports view. */
export function SavedExportTab() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const [ctx] = useScope();
  const id = useAgencyId();
  const report = params.get("report");
  const requested = params.get("view");
  const view: View = (SAVED_REPORT_TYPES as readonly string[]).includes(report ?? "")
    ? "reports"
    : requested === "saved" || requested === "reports"
      ? requested
      : "summary";
  if (id == null) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <nav aria-label={t("saved.title")} style={SCREEN_STRIP_STYLE}>
        {VIEWS.map(([v, key]) => (
          <Link
            key={v}
            to={`/agencies/${id}/saved${mergeSearch(scopeToQueryString(ctx), v === "summary" ? {} : { view: v })}`}
            aria-current={v === view ? "page" : undefined}
            style={screenStripLinkStyle(v === view)}
          >
            {t(key)}
          </Link>
        ))}
      </nav>
      <div style={{ flex: 1, minHeight: 0 }}>
        {view === "reports" ? <AnalysisTab reportTypes={SAVED_REPORT_TYPES} /> : <ReportsHomeTab />}
      </div>
    </div>
  );
}
