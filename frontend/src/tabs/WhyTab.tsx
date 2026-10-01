import { lazy } from "react";
import { WHY_REPORT_TYPES } from "../routes/destinations";
import { loadAnalysisTab } from "../routes/lazyTabs";

const AnalysisTab = lazy(loadAnalysisTab);

export function WhyTab() {
  return <AnalysisTab reportTypes={WHY_REPORT_TYPES} />;
}
