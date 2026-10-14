import { lazy } from "react";
import { useTranslation } from "react-i18next";
import { ScopeHeaderProvider, ScopePageHeader } from "../components/scope/ScopePageHeader";
import { TIME_REPORT_TYPES } from "../routes/destinations";
import { loadAnalysisTab } from "../routes/lazyTabs";

const AnalysisTab = lazy(loadAnalysisTab);

export function TimeTab() {
  const { t } = useTranslation();
  return (
    <ScopeHeaderProvider>
      <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <ScopePageHeader title={t("nav.time")} />
        <div style={{ flex: 1, minHeight: 0 }}>
          <AnalysisTab reportTypes={TIME_REPORT_TYPES} />
        </div>
      </div>
    </ScopeHeaderProvider>
  );
}
