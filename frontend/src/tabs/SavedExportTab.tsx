import { lazy } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, useSearchParams } from "react-router-dom";
import { useAgencyId } from "../api/useAgencyId";
import { SAVED_REPORT_TYPES } from "../routes/analysisRoutes";
import { loadAnalysisTab, loadReportsHomeTab } from "../routes/lazyTabs";

const ReportsHomeTab = lazy(loadReportsHomeTab);
const AnalysisTab = lazy(loadAnalysisTab);

type View = "summary" | "saved" | "reports";

const VIEWS: [View, string][] = [
  ["summary", "saved.view_summary"],
  ["saved", "saved.view_saved"],
  ["reports", "saved.view_reports"],
];

/** Saved & export: the printable period summary, the saved analyses list,
 *  and the export-style report types (council summary, delay reference). */
export function SavedExportTab() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
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
      <nav
        aria-label={t("saved.title")}
        style={{ display: "flex", gap: 2, borderBottom: "1px solid var(--border-soft)", marginBottom: 12 }}
      >
        {VIEWS.map(([v, key]) => (
          <NavLink
            key={v}
            to={`/agencies/${id}/saved${v === "summary" ? "" : `?view=${v}`}`}
            aria-current={v === view ? "page" : undefined}
            style={{
              padding: "9px 12px 8px",
              fontSize: "var(--text-sm)",
              textDecoration: "none",
              color: v === view ? "var(--text-primary)" : "var(--text-secondary)",
              borderBottom: `2px solid ${v === view ? "var(--accent)" : "transparent"}`,
            }}
          >
            {t(key)}
          </NavLink>
        ))}
      </nav>
      <div style={{ flex: 1, minHeight: 0 }}>
        {view === "reports" ? <AnalysisTab reportTypes={SAVED_REPORT_TYPES} /> : <ReportsHomeTab />}
      </div>
    </div>
  );
}
