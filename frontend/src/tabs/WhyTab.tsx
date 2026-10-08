import { lazy } from "react";
import { useTranslation } from "react-i18next";
import { PageHeader } from "../components/ui/PageHeader";
import { WHY_REPORT_TYPES } from "../routes/destinations";
import { loadAnalysisTab } from "../routes/lazyTabs";

const AnalysisTab = lazy(loadAnalysisTab);

export function WhyTab() {
  const { t } = useTranslation();
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <PageHeader title={t("nav.why")} />
      <div style={{ flex: 1, minHeight: 0 }}>
        <AnalysisTab reportTypes={WHY_REPORT_TYPES} />
      </div>
    </div>
  );
}
