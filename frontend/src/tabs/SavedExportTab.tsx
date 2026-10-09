import { lazy } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, useLocation, useSearchParams } from "react-router-dom";
import { scopeToQueryString, useScope } from "../api/scope";
import { useAgencyId } from "../api/useAgencyId";
import { REPORTS_VIEWS, SAVED_REPORT_TYPES, destHref, reportsView, savedTarget } from "../routes/destinations";
import { loadAnalysisTab, loadReportsHomeTab } from "../routes/lazyTabs";
import { SCREEN_STRIP_STYLE, screenStripLinkStyle } from "../components/screenStrip";
import { PageHeader } from "../components/ui/PageHeader";

const ReportsHomeTab = lazy(loadReportsHomeTab);
const AnalysisTab = lazy(loadAnalysisTab);

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
  const view = reportsView(doc, params.get("report"));
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* One heading for every document, above the strip that picks one; the
          line under it says what the open document holds. */}
      <PageHeader title={t("saved.title")} subtitle={t(`saved.subtitle_${view}`)} />
      <nav aria-label={t("saved.title")} style={SCREEN_STRIP_STYLE}>
        {REPORTS_VIEWS.map(({ view: v, labelKey, extra }) => (
          <Link
            key={v}
            to={destHref(id, "reports", scopeToQueryString(ctx), extra)}
            aria-current={v === view ? "page" : undefined}
            style={screenStripLinkStyle(v === view)}
          >
            {t(labelKey)}
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
