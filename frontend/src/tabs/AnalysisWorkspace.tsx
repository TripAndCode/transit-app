import { lazy } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useLocation, useParams, useSearchParams } from "react-router-dom";
import { useAgencyId } from "../api/useAgencyId";
import { LensTabs } from "../components/LensTabs";
import { isLensId, lensHref, lensReportTypes, reportHref } from "../routes/analysisRoutes";
import { loadAnalysisTab, loadNetworkTab, loadOverviewTab, loadRouteAnalysisTab } from "../routes/lazyTabs";
import { REPORT_TYPE_IDS } from "./reportTypes";

const OverviewTab = lazy(loadOverviewTab);
const RouteAnalysisTab = lazy(loadRouteAnalysisTab);
const NetworkTab = lazy(loadNetworkTab);
const AnalysisTab = lazy(loadAnalysisTab);

/** The Analysis destination: a lens strip over the screen each lens hosts.
 *  An old `analysis/:reportType` bookmark shares this route's shape, so a
 *  segment that is a report type is forwarded to the lens that hosts it. */
export function AnalysisWorkspace() {
  const { t } = useTranslation();
  const { lens } = useParams();
  const { search } = useLocation();
  const [params, setParams] = useSearchParams();
  const id = useAgencyId();
  if (id == null) return null;
  if (!isLensId(lens)) {
    const target = (REPORT_TYPE_IDS as readonly string[]).includes(lens ?? "")
      ? reportHref(id, lens ?? "", search)
      : lensHref(id, "overview", search);
    return <Navigate to={target} replace />;
  }
  if (lens === "predict") return <Navigate to={lensHref(id, "overview", search)} replace />;
  const mode = params.get("mode") === "agencies" ? "agencies" : "periods";
  function setMode(next: "periods" | "agencies") {
    setParams((prev) => {
      const n = new URLSearchParams(prev);
      if (next === "agencies") n.set("mode", "agencies");
      else n.delete("mode");
      return n;
    });
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <LensTabs agencyId={id} active={lens} />
      {lens === "compare" && (
        <div
          role="group"
          aria-label={t("lens.compare")}
          style={{
            display: "inline-flex",
            alignSelf: "flex-start",
            gap: 2,
            padding: 2,
            marginBottom: 12,
            border: "1px solid var(--border-soft)",
            borderRadius: "var(--radius)",
            background: "var(--bg-surface)",
          }}
        >
          {(["periods", "agencies"] as const).map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={mode === m}
              onClick={() => setMode(m)}
              style={{
                padding: "5px 12px",
                fontSize: "var(--text-sm)",
                border: "none",
                borderRadius: "var(--radius)",
                background: mode === m ? "var(--accent-soft)" : "transparent",
                color: mode === m ? "var(--accent-strong)" : "var(--text-secondary)",
                fontWeight: mode === m ? 500 : 400,
              }}
            >
              {t(m === "periods" ? "compare.mode_periods" : "compare.mode_agencies")}
            </button>
          ))}
        </div>
      )}
      <div style={{ flex: 1, minHeight: 0 }}>
        {lens === "overview" && <OverviewTab />}
        {lens === "where" && <RouteAnalysisTab />}
        {lens === "compare" && mode === "agencies" && <NetworkTab />}
        {lens === "compare" && mode === "periods" && <AnalysisTab reportTypes={lensReportTypes("compare")} />}
        {(lens === "when" || lens === "why" || lens === "rider") && (
          <AnalysisTab key={lens} reportTypes={lensReportTypes(lens)} />
        )}
      </div>
    </div>
  );
}
