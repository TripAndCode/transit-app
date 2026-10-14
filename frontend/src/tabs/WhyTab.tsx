import { lazy } from "react";
import { useTranslation } from "react-i18next";
import { ScopeHeaderProvider, ScopePageHeader } from "../components/scope/ScopePageHeader";
import { WHY_REPORT_TYPES } from "../routes/destinations";
import { loadAnalysisTab } from "../routes/lazyTabs";

const AnalysisTab = lazy(loadAnalysisTab);

export function WhyTab() {
  const { t } = useTranslation();
  return (
    <ScopeHeaderProvider>
      <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <ScopePageHeader title={t("nav.why")} />
        <div style={{ flex: 1, minHeight: 0 }}>
          <AnalysisTab reportTypes={WHY_REPORT_TYPES} />
        </div>
      </div>
    </ScopeHeaderProvider>
  );
}
