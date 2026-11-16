import { lazy } from "react";
import { useTranslation } from "react-i18next";
import { ScopeHeaderProvider, ScopePageHeader } from "../components/scope/ScopePageHeader";
import { WHY_REPORT } from "../routes/destinations";
import { loadSingleReportScreen } from "../routes/lazyTabs";

const SingleReportScreen = lazy(loadSingleReportScreen);

export function WhyTab() {
  const { t } = useTranslation();
  return (
    <ScopeHeaderProvider>
      <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <ScopePageHeader title={t("nav.why")} />
        <div style={{ flex: 1, minHeight: 0 }}>
          <SingleReportScreen reportType={WHY_REPORT} />
        </div>
      </div>
    </ScopeHeaderProvider>
  );
}
