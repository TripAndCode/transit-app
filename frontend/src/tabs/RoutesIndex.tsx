import { lazy } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { useAgencyId } from "../api/useAgencyId";
import { PageHeader } from "../components/ui/PageHeader";
import { ROUTES_REPORT_TYPES } from "../routes/destinations";
import { loadAnalysisTab } from "../routes/lazyTabs";

const AnalysisTab = lazy(loadAnalysisTab);

/** The Routes list: the route ranking reports, each row of which opens its
 *  route. `sort` names the report that opens when none is chosen. */
export function RoutesIndex() {
  const { t } = useTranslation();
  const id = useAgencyId();
  const [params] = useSearchParams();
  if (id == null) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <PageHeader title={t("nav.routes")} />
      <div style={{ flex: 1, minHeight: 0 }}>
        <AnalysisTab reportTypes={ROUTES_REPORT_TYPES} defaultReport={params.get("sort")} />
      </div>
    </div>
  );
}
